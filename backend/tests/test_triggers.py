"""문서 변경 트리거 (ARCHITECTURE.md "자동 임베딩 파이프라인", ADR-001·ADR-009).

이 파일이 검증하는 주장은 하나다 — **애플리케이션은 `documents`만 UPDATE하는데,
버전 이력과 임베딩 잡이 같은 트랜잭션 안에서 생긴다.** 그래서 아래 테스트들은
`document_versions`·`embedding_jobs`에 직접 INSERT하지 않는다. 트리거가 만든 것만 센다.

트리거·NOTIFY는 원리상 Mock으로 확인할 수 없으므로 실제 pgvector 컨테이너에
`backend/migrations/`를 적용한 `migrated_db` 픽스처 위에서 돈다 (CLAUDE.md CRITICAL).

> NOTIFY 테스트가 통과한다고 해서 파이프라인이 NOTIFY에 의존해도 된다는 뜻은 아니다.
> 여기는 DB 직결이고, 실 환경은 OpenProxy를 경유해 동작이 보장되지 않는다. 워커는
> 폴링을 주 경로로 삼는다 (ADR-009).
"""

import psycopg
import pytest

CHANNEL = "embedding_jobs"


def insert_document(
    conn: psycopg.Connection,
    content: str = "v1 추출 텍스트",
    content_hash: str = "sha256:v1",
):
    """API의 업로드 핸들러가 하는 일과 같다 — `documents`에 한 건 넣을 뿐이다."""
    row = conn.execute(
        """
        INSERT INTO documents (title, content_type, content, content_hash, owner_id)
        VALUES ('정합성 검증 보고서', 'md', %s, %s, 'alice')
        RETURNING id
        """,
        (content, content_hash),
    ).fetchone()
    return row[0]


def edit_content(conn: psycopg.Connection, doc_id, content: str, content_hash: str) -> None:
    """API의 PUT 핸들러가 하는 일과 같다 (ARCHITECTURE "인라인 편집").

    `document_versions`·`embedding_jobs`가 이 함수에 없다는 것이 핵심이다.
    """
    conn.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %s, content_hash = %s, updated_at = now()
         WHERE id = %s
        """,
        (content, content_hash, doc_id),
    )


def job_statuses(conn: psycopg.Connection, doc_id) -> list[str]:
    rows = conn.execute(
        "SELECT status FROM embedding_jobs WHERE document_id = %s ORDER BY id", (doc_id,)
    ).fetchall()
    return [r[0] for r in rows]


def history(conn: psycopg.Connection, doc_id) -> list[tuple]:
    return conn.execute(
        """
        SELECT version, content, content_hash FROM document_versions
        WHERE document_id = %s ORDER BY version
        """,
        (doc_id,),
    ).fetchall()


@pytest.fixture
def conn(migrated_db: str):
    with psycopg.connect(migrated_db, autocommit=True) as c:
        yield c


@pytest.fixture
def listener(migrated_db: str):
    """`LISTEN embedding_jobs`를 걸어둔 별도 커넥션.

    알림은 커밋 시점에 발행되므로, 쓰기 세션과 반드시 분리되어야 관측할 수 있다.
    """
    with psycopg.connect(migrated_db, autocommit=True) as c:
        c.execute(f"LISTEN {CHANNEL}")
        yield c


def test_insert_creates_one_pending_job_and_records_v1(conn: psycopg.Connection):
    """업로드 코드에 임베딩 호출이 없어도 파이프라인이 기동한다 (ADR-001).

    v1 이력이 INSERT 시점부터 남는 것이 이 설계의 특징이다 — 문서 생성 직후부터
    v1 조회가 가능하고, 이력이 append-only로 완결된다.
    """
    doc_id = insert_document(conn)

    assert job_statuses(conn, doc_id) == ["pending"]
    assert history(conn, doc_id) == [(1, "v1 추출 텍스트", "sha256:v1")]


def test_consecutive_edits_pile_up_history_but_coalesce_into_one_job(conn: psycopg.Connection):
    """DB 계층 코얼레싱 — 이력은 쌓이고 잡은 합쳐진다 (ADR-001).

    워커가 집어가기 전에 두 번 수정해도 pending 잡은 1건이다. 잡이 "이 문서는
    재임베딩이 필요하다"는 신호만 담고 본문 페이로드를 담지 않기에 가능하다 —
    워커는 처리 시점의 최신 content를 읽으므로 중간 버전을 임베딩할 이유가 없다.
    """
    doc_id = insert_document(conn)

    edit_content(conn, doc_id, "v2 추출 텍스트", "sha256:v2")
    edit_content(conn, doc_id, "v3 추출 텍스트", "sha256:v3")

    assert [h[0] for h in history(conn, doc_id)] == [1, 2, 3]
    assert job_statuses(conn, doc_id) == ["pending"]


def test_metadata_only_update_does_not_fire_the_trigger(conn: psycopg.Connection):
    """`UPDATE OF content_hash`의 값어치.

    제목·태그만 바꾼 UPDATE로 재임베딩이 돌면 낭비이고, 본문이 그대로인데 이력에
    새 버전이 쌓여 이력 자체가 거짓말이 된다.
    """
    doc_id = insert_document(conn)

    conn.execute(
        "UPDATE documents SET title = '제목만 고침', tags = ARRAY['보안'] WHERE id = %s",
        (doc_id,),
    )

    assert job_statuses(conn, doc_id) == ["pending"]  # INSERT 때 만들어진 것 그대로
    assert [h[0] for h in history(conn, doc_id)] == [1]


def test_editing_content_returns_the_status_to_pending(conn: psycopg.Connection):
    """워커가 끝내둔 문서를 고치면 다시 대기 상태로 돌아간다.

    이 전환이 `/admin/status`의 정합성 카운터가 올랐다가 0으로 수렴하는 장면의 출발점이다.
    """
    doc_id = insert_document(conn)
    conn.execute("UPDATE documents SET embedding_status = 'ready' WHERE id = %s", (doc_id,))

    edit_content(conn, doc_id, "v2 추출 텍스트", "sha256:v2")

    (status,) = conn.execute(
        "SELECT embedding_status FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    assert status == "pending"


def test_reembed_path_makes_a_job_without_bumping_the_version(conn: psycopg.Connection):
    """`POST /api/documents/{id}/reembed`의 유일한 수단 (ARCHITECTURE "임베딩 실패 복구").

    `UPDATE OF content_hash`는 **값이 바뀌지 않아도 SET 절에 컬럼이 언급되면 발화한다.**
    이 성질이 있어야 애플리케이션이 `embedding_jobs`를 직접 건드리지 않고도 실패한
    문서를 되살릴 수 있다 (CLAUDE.md CRITICAL). 셋을 함께 확인한다 —
    잡은 새로 생기고, 버전은 오르지 않으며, 이력은 중복되지 않는다.
    """
    doc_id = insert_document(conn)
    # 3회 재시도 끝에 error로 끝난 상태를 재현한다.
    conn.execute("UPDATE embedding_jobs SET status = 'error' WHERE document_id = %s", (doc_id,))
    conn.execute("UPDATE documents SET embedding_status = 'error' WHERE id = %s", (doc_id,))

    conn.execute("UPDATE documents SET content_hash = content_hash WHERE id = %s", (doc_id,))

    version, status = conn.execute(
        "SELECT version, embedding_status FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    assert job_statuses(conn, doc_id) == ["error", "pending"]
    assert (version, status) == (1, "pending")
    assert history(conn, doc_id) == [(1, "v1 추출 텍스트", "sha256:v1")]


def test_editing_while_processing_creates_a_new_pending_job(conn: psycopg.Connection):
    """처리 중 재수정 — 코얼레싱이 막는 것은 `pending`뿐이다.

    워커가 집어간 잡(`processing`)은 이미 낡은 내용을 임베딩하고 있으므로, 새 잡이
    생겨야 최신 내용이 반영된다. 낡은 결과는 워커가 커밋 직전 `content_hash`를
    재확인해 스스로 폐기한다 (ARCHITECTURE 워커 루프 3번).
    """
    doc_id = insert_document(conn)
    conn.execute(
        "UPDATE embedding_jobs SET status = 'processing' WHERE document_id = %s", (doc_id,)
    )

    edit_content(conn, doc_id, "v2 추출 텍스트", "sha256:v2")

    assert job_statuses(conn, doc_id) == ["processing", "pending"]


def test_a_single_insert_fires_the_trigger_exactly_once(
    conn: psycopg.Connection, listener: psycopg.Connection
):
    """재귀 발화가 없다.

    트리거 2단계가 같은 테이블(`documents`)을 UPDATE하므로 재진입 위험이 있다.
    `WHEN (pg_trigger_depth() = 0)`과 `UPDATE OF content_hash`가 함께 막으며,
    막지 못하면 이력·잡·알림이 문서 하나에 여러 벌 생기는 것으로 드러난다.
    """
    doc_id = insert_document(conn)

    counts = conn.execute(
        """
        SELECT (SELECT count(*) FROM document_versions WHERE document_id = %(id)s),
               (SELECT count(*) FROM embedding_jobs    WHERE document_id = %(id)s)
        """,
        {"id": doc_id},
    ).fetchone()

    assert counts == (1, 1)
    assert [n.payload for n in listener.notifies(timeout=5, stop_after=1)] == [str(doc_id)]
    # 첫 알림 이후로 더 오는 것이 없어야 한다.
    assert list(listener.notifies(timeout=0.5)) == []


def test_commit_publishes_a_notify_carrying_the_document_id(
    conn: psycopg.Connection, listener: psycopg.Connection
):
    """워커를 깨우는 최적화 경로 (ADR-009).

    payload가 문서 id여야 워커가 어느 문서인지 알 수 있다. 다만 이 알림이 유실돼도
    폴링이 처리하므로, 이것은 지연 단축 수단이지 전달 보장 수단이 아니다.
    """
    doc_id = insert_document(conn)

    received = list(listener.notifies(timeout=5, stop_after=1))

    assert [(n.channel, n.payload) for n in received] == [(CHANNEL, str(doc_id))]


def test_a_rolled_back_change_publishes_nothing(migrated_db: str, listener: psycopg.Connection):
    """유령 이벤트가 없다 (ADR-001).

    `pg_notify`는 커밋 시에만 발행되므로, 롤백된 문서의 알림은 애초에 나가지 않는다.
    롤백을 먼저 하고 커밋을 뒤에 하므로, 유령이 있었다면 그것이 먼저 도착한다.
    """
    with psycopg.connect(migrated_db) as writer:  # autocommit 아님 — 명시적 트랜잭션
        rolled_back = insert_document(writer)
        writer.rollback()
        committed = insert_document(writer)
        writer.commit()

    payloads = [n.payload for n in listener.notifies(timeout=5, stop_after=1)]

    assert payloads == [str(committed)]
    assert str(rolled_back) not in payloads


def test_trigger_definition_keeps_its_three_safety_conditions(conn: psycopg.Connection):
    """정의 자체를 못 박는다.

    아래 셋은 지금 스키마에서 동작으로 관측되지 않는 구간이 있어(트리거가 하나뿐이라
    `pg_trigger_depth()`가 1이 되는 경로가 없다) 정의를 직접 확인한다. 조용히 빠지면
    아웃박스가 행 확정 전에 기록되거나(BEFORE), 제목만 고쳐도 재임베딩이 돌거나(UPDATE),
    재진입 방어가 사라진다.
    """
    (definition,) = conn.execute(
        """
        SELECT pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'documents' AND t.tgname = 'trg_documents_content_changed'
        """
    ).fetchone()

    assert "AFTER INSERT OR UPDATE OF content_hash" in definition
    assert "FOR EACH ROW" in definition
    assert "pg_trigger_depth() = 0" in definition
