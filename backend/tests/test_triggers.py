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

from app.embeddings.fake import FakeProvider
from app.vectors import to_pgvector_literal

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


def mark_document_ready(
    conn: psycopg.Connection,
    doc_id,
    chunks: list[str],
    *,
    version: int = 1,
    vectors: list[list[float]] | None = None,
) -> None:
    """워커 finalize의 청크 INSERT → ready 전환 순서를 DB 공개 인터페이스로 재현한다."""
    vectors = vectors or FakeProvider().embed(chunks)
    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        conn.execute(
            """
            INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s::vector)
            """,
            (doc_id, version, index, chunk, to_pgvector_literal(vector)),
        )
    conn.execute("UPDATE documents SET embedding_status = 'ready' WHERE id = %s", (doc_id,))


def unit_vector(coordinate_index: int) -> list[float]:
    vector = [0.0] * 1024
    vector[coordinate_index] = 1.0
    return vector


def edges_for(conn: psycopg.Connection, doc_id) -> list[tuple]:
    return conn.execute(
        """
        SELECT src_document_id, dst_document_id, kind,
               src_chunk_index, dst_chunk_index, score
        FROM document_edges
        WHERE src_document_id = %s OR dst_document_id = %s
        ORDER BY src_document_id, dst_document_id, kind,
                 src_chunk_index NULLS FIRST, dst_chunk_index NULLS FIRST
        """,
        (doc_id, doc_id),
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


def test_ready_document_builds_edges_inside_the_database(conn: psycopg.Connection):
    """워커가 청크를 확정하고 ready로 바꾸면 애플리케이션 INSERT 없이 관계가 생긴다."""
    first_id = insert_document(conn, "공통 토큰 alpha beta", "sha256:edge-first")
    second_id = insert_document(conn, "공통 토큰 alpha gamma", "sha256:edge-second")
    mark_document_ready(conn, first_id, ["공통 토큰 alpha beta"])

    mark_document_ready(conn, second_id, ["공통 토큰 alpha gamma"])

    (count,) = conn.execute(
        """
        SELECT count(*) FROM document_edges
        WHERE src_document_id = %s OR dst_document_id = %s
        """,
        (second_id, second_id),
    ).fetchone()
    assert count > 0


def test_non_directional_edges_are_inserted_in_both_directions_without_self_edges(
    conn: psycopg.Connection,
):
    first_id = insert_document(conn, "first", "sha256:edge-direction-first")
    second_id = insert_document(conn, "second", "sha256:edge-direction-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(0)])

    rows = edges_for(conn, second_id)

    assert {(row[0], row[1]) for row in rows} == {
        (first_id, second_id),
        (second_id, first_id),
    }
    assert all(row[0] != row[1] for row in rows)


def test_reembedding_replaces_all_edges_touching_the_document(conn: psycopg.Connection):
    first_id = insert_document(conn, "first", "sha256:edge-reembed-first")
    second_id = insert_document(conn, "second", "sha256:edge-reembed-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(0)])
    first_edges = edges_for(conn, second_id)
    conn.execute(
        "UPDATE document_edges SET score = 0.123 WHERE src_document_id = %s OR dst_document_id = %s",
        (second_id, second_id),
    )

    conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (second_id,))
    conn.execute("UPDATE documents SET embedding_status = 'processing' WHERE id = %s", (second_id,))
    mark_document_ready(conn, second_id, ["second changed"], vectors=[unit_vector(1)])

    replaced_edges = edges_for(conn, second_id)
    assert len(replaced_edges) == len(first_edges)
    assert all(row[5] != pytest.approx(0.123) for row in replaced_edges)


def test_edge_kind_distinguishes_overlaps_from_related_and_never_emits_points_to(
    conn: psycopg.Connection,
):
    """낮은 매칭 비율은 폐기된 points_to가 아니라 related로 폴백한다 (ADR-029)."""
    overlap_id = insert_document(conn, "overlap", "sha256:kind-overlap")
    partial_id = insert_document(conn, "partial", "sha256:kind-partial")
    source_id = insert_document(conn, "source", "sha256:kind-source")
    mark_document_ready(
        conn,
        overlap_id,
        ["same zero", "same one"],
        vectors=[unit_vector(0), unit_vector(1)],
    )
    mark_document_ready(conn, partial_id, ["only zero"], vectors=[unit_vector(0)])
    # source의 두 번째 청크에서 partial을 top-10 밖으로 밀어 매칭 비율을 1/2로 만든다.
    near_one = unit_vector(1)
    near_one[2] = 0.1
    for index in range(10):
        decoy_id = insert_document(conn, f"decoy {index}", f"sha256:kind-decoy:{index}")
        mark_document_ready(conn, decoy_id, [f"decoy {index}"], vectors=[near_one])
    mark_document_ready(
        conn,
        source_id,
        ["source zero", "source one"],
        vectors=[unit_vector(0), unit_vector(1)],
    )

    kinds = {
        dst_id: kind
        for dst_id, kind in conn.execute(
            """
            SELECT dst_document_id, kind FROM document_edges
            WHERE src_document_id = %s
            """,
            (source_id,),
        ).fetchall()
    }

    assert kinds[overlap_id] == "overlaps"
    assert kinds[partial_id] == "related"
    assert "points_to" not in kinds.values()


def test_deleting_a_document_cascades_trigger_generated_edges(conn: psycopg.Connection):
    first_id = insert_document(conn, "first", "sha256:edge-delete-first")
    second_id = insert_document(conn, "second", "sha256:edge-delete-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(0)])

    conn.execute("DELETE FROM documents WHERE id = %s", (second_id,))

    assert edges_for(conn, second_id) == []


def test_metadata_update_does_not_rebuild_edges(conn: psycopg.Connection):
    first_id = insert_document(conn, "first", "sha256:edge-metadata-first")
    second_id = insert_document(conn, "second", "sha256:edge-metadata-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(0)])
    conn.execute(
        "UPDATE document_edges SET score = 0.123 WHERE src_document_id = %s OR dst_document_id = %s",
        (second_id, second_id),
    )
    before = edges_for(conn, second_id)

    conn.execute(
        "UPDATE documents SET title = 'renamed', tags = ARRAY['changed'] WHERE id = %s",
        (second_id,),
    )

    assert edges_for(conn, second_id) == before


def test_ready_to_ready_update_does_not_reenter_the_trigger(conn: psycopg.Connection):
    first_id = insert_document(conn, "first", "sha256:edge-reentry-first")
    second_id = insert_document(conn, "second", "sha256:edge-reentry-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(0)])
    conn.execute(
        "UPDATE document_edges SET score = 0.123 WHERE src_document_id = %s OR dst_document_id = %s",
        (second_id, second_id),
    )

    conn.execute("UPDATE documents SET embedding_status = 'ready' WHERE id = %s", (second_id,))

    assert all(row[5] == pytest.approx(0.123) for row in edges_for(conn, second_id))


@pytest.mark.parametrize("deprecated_kind", ["points_to", "broader"])
def test_edges_reject_kinds_removed_by_adr_029(
    conn: psycopg.Connection, deprecated_kind: str
):
    first_id = insert_document(conn, "first", f"sha256:removed-kind-first:{deprecated_kind}")
    second_id = insert_document(conn, "second", f"sha256:removed-kind-second:{deprecated_kind}")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO document_edges
                (src_document_id, dst_document_id, kind, score)
            VALUES (%s, %s, %s, 0.5)
            """,
            (first_id, second_id, deprecated_kind),
        )


def test_edges_trigger_definition_and_function_settings_are_scoped(conn: psycopg.Connection):
    definition, config = conn.execute(
        """
        SELECT pg_get_triggerdef(t.oid), p.proconfig
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE c.relname = 'documents' AND t.tgname = 'trg_build_document_edges'
        """
    ).fetchone()

    assert "AFTER UPDATE OF embedding_status" in definition
    assert "new.embedding_status = 'ready'" in definition.lower()
    assert "old.embedding_status IS DISTINCT FROM 'ready'".lower() in definition.lower()
    assert set(config) == {"hnsw.ef_search=200", "random_page_cost=1.1"}


def test_edge_neighbor_constant_probe_can_use_the_hnsw_index(conn: psycopg.Connection):
    """§14의 상관 LATERAL과 달리 함수의 청크별 상수 프로브는 HNSW가 가능한 형태다."""
    first_id = insert_document(conn, "first", "sha256:edge-plan-first")
    second_id = insert_document(conn, "second", "sha256:edge-plan-second")
    mark_document_ready(conn, first_id, ["first"], vectors=[unit_vector(0)])
    mark_document_ready(conn, second_id, ["second"], vectors=[unit_vector(1)])
    probe = to_pgvector_literal(unit_vector(0))

    with conn.transaction():
        conn.execute("SET LOCAL hnsw.ef_search = 200")
        conn.execute("SET LOCAL random_page_cost = 1.1")
        # 작은 테스트 테이블에서 비용 선택이 아니라 쿼리 형태의 인덱스 사용 능력을 본다.
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                """
                EXPLAIN (COSTS OFF)
                SELECT candidate.document_id
                FROM document_chunks candidate
                WHERE candidate.document_id <> %s
                ORDER BY candidate.embedding <=> %s::vector
                LIMIT 10
                """,
                (second_id, probe),
            ).fetchall()
        )

    assert "Index Scan using idx_chunks_embedding" in plan
