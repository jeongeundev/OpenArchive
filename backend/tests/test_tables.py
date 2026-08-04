"""스키마 제약 (ARCHITECTURE.md "DB 스키마", ADR-001·ADR-003).

여기서 검증하는 것은 테이블의 "존재"가 아니라 **제약이 실제로 막아주는가**다.
빈 본문 차단·벡터 차원 고정·CASCADE·코얼레싱은 전부 DB가 판정하므로 Mock으로는
확인할 수 없다. 실제 pgvector 컨테이너에 `backend/migrations/`를 적용한
`migrated_db` 픽스처 위에서 돈다.

문서를 INSERT하면 003_triggers.sql의 트리거가 v1 이력과 pending 잡을 함께 만든다.
여기서는 그것을 "이미 존재하는 행"으로만 다루고, 트리거의 동작 자체는 검증하지
않는다 — 그쪽은 test_triggers.py의 몫이다.
`embedding_jobs` 직접 INSERT 금지 규칙(CLAUDE.md)은 애플리케이션 코드를 대상으로
하며, 제약 자체가 검증 대상인 여기서는 직접 INSERT가 유일한 수단이다.
"""

import psycopg
import pytest

CORE_TABLES = {"documents", "document_versions", "document_chunks", "embedding_jobs"}

# 임베딩 차원은 vector(1024) 고정이다 (ADR-003).
VECTOR_1024 = "[" + ",".join(["0.1"] * 1024) + "]"


def insert_document(
    conn: psycopg.Connection,
    content: str = "원본과 벡터의 정합성을 확인한다.",
    content_hash: str = "sha256:doc-1",
) -> str:
    """documents에 NOT NULL 컬럼만 채워 한 건 넣고 id를 반환한다."""
    row = conn.execute(
        """
        INSERT INTO documents (title, content_type, content, content_hash, owner_id)
        VALUES ('정합성 검증 보고서', 'md', %s, %s, 'alice')
        RETURNING id
        """,
        (content, content_hash),
    ).fetchone()
    return row[0]


@pytest.fixture
def conn(migrated_db: str):
    """autocommit 연결.

    제약 위반은 트랜잭션을 abort시키므로, 한 테스트에서 "실패 → 이어서 확인"을
    하려면 문장 하나가 트랜잭션 하나여야 한다.
    """
    with psycopg.connect(migrated_db, autocommit=True) as c:
        yield c


def test_vector_extension_and_core_tables_exist(conn: psycopg.Connection):
    (has_vector,) = conn.execute(
        "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()

    assert has_vector == 1
    assert CORE_TABLES <= {t[0] for t in tables}


@pytest.mark.parametrize("blank", ["", "   ", "\t\n  \n"])
def test_blank_content_is_rejected(conn: psycopg.Connection, blank: str):
    """텍스트를 추출하지 못한 문서는 저장되지 않는다.

    빈 본문은 임베딩할 것이 없어 검색에 영원히 잡히지 않는 유령 행이 된다.
    API가 400으로 막지만(ARCHITECTURE "빈 파싱 결과 처리"), DB에서도 막는 이중 방어다.
    """
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        insert_document(conn, content=blank)

    assert "documents_content_not_blank" in str(exc.value)


def test_document_defaults_are_filled_in(conn: psycopg.Connection):
    doc_id = insert_document(conn)

    version, status, visibility, tags, created_at, updated_at = conn.execute(
        """
        SELECT version, embedding_status, visibility, tags, created_at, updated_at
        FROM documents WHERE id = %s
        """,
        (doc_id,),
    ).fetchone()

    assert version == 1
    assert status == "pending"
    assert visibility == "public"
    assert tags == []
    assert created_at is not None
    assert updated_at is not None


def test_chunk_embedding_dimension_is_fixed_at_1024(conn: psycopg.Connection):
    """프로바이더가 바뀌어도 차원은 바뀌지 않는다 (ADR-003)."""
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.DataException) as exc:
        conn.execute(
            """
            INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
            VALUES (%s, 1, 0, '차원이 맞지 않는 청크', %s)
            """,
            (doc_id, "[1,2,3]"),
        )

    assert "1024 dimensions" in str(exc.value)


def test_chunk_index_is_unique_per_document(conn: psycopg.Connection):
    doc_id = insert_document(conn)
    insert_chunk = """
        INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
        VALUES (%s, 1, 0, %s, %s)
    """

    conn.execute(insert_chunk, (doc_id, "첫 청크", VECTOR_1024))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert_chunk, (doc_id, "같은 자리에 덮어쓰려는 청크", VECTOR_1024))


def test_version_history_rejects_a_duplicate_version(conn: psycopg.Connection):
    """(document_id, version)이 PK다 — 같은 버전 번호가 두 번 기록되지 않는다.

    트리거가 `ON CONFLICT (document_id, version) DO NOTHING`으로 재실행 안전을
    얻는데, 그 충돌 대상이 이 PK다.

    v1은 트리거가 이미 기록했으므로 v2로 확인한다 — 검증 대상은 PK이지 트리거가 아니다.
    """
    doc_id = insert_document(conn)
    insert_version = """
        INSERT INTO document_versions (document_id, version, content, content_hash)
        VALUES (%s, 2, %s, 'sha256:v2')
    """

    conn.execute(insert_version, (doc_id, "v2 추출 텍스트"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert_version, (doc_id, "같은 버전 번호로 다시"))


def test_deleting_a_document_cascades_to_versions_chunks_and_jobs(conn: psycopg.Connection):
    """삭제 정합성 — 문서가 사라지면 벡터도 같은 트랜잭션에서 사라진다.

    워커 개입 없이 DB가 보장하므로, 문서만 지워지고 벡터가 남는 상태가 구조적으로 없다.
    """
    doc_id = insert_document(conn)  # 트리거가 v1 이력과 pending 잡을 함께 만든다
    conn.execute(
        """
        INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
        VALUES (%s, 1, 0, '첫 청크', %s)
        """,
        (doc_id, VECTOR_1024),
    )
    count_children = """
        SELECT (SELECT count(*) FROM document_versions WHERE document_id = %(id)s),
               (SELECT count(*) FROM document_chunks   WHERE document_id = %(id)s),
               (SELECT count(*) FROM embedding_jobs    WHERE document_id = %(id)s)
    """
    assert conn.execute(count_children, {"id": doc_id}).fetchone() == (1, 1, 1)

    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

    assert conn.execute(count_children, {"id": doc_id}).fetchone() == (0, 0, 0)


def test_a_document_can_have_only_one_pending_job(conn: psycopg.Connection):
    """DB 계층 코얼레싱 (ADR-001).

    트리거가 `ON CONFLICT DO NOTHING`으로 잡을 생성하는데, 이 파셜 유니크 인덱스가
    그 충돌 대상이다. 없으면 연속 수정마다 잡이 무한정 쌓인다.
    """
    doc_id = insert_document(conn)  # 트리거가 pending 잡 1건을 만든다

    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        conn.execute("INSERT INTO embedding_jobs (document_id) VALUES (%s)", (doc_id,))

    assert "uq_pending_job_per_doc" in str(exc.value)


def test_a_finished_job_frees_the_slot_for_a_new_pending_job(conn: psycopg.Connection):
    """제약 대상은 `status = 'pending'` 행뿐이다.

    파셜이 아닌 유니크 인덱스였다면 문서는 평생 잡을 한 번만 가질 수 있고,
    재임베딩이 영영 불가능해진다. 이 확인이 파셜이라는 것의 증거다.
    """
    doc_id = insert_document(conn)  # 트리거가 pending 잡 1건을 만든다

    conn.execute("UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (doc_id,))
    conn.execute("INSERT INTO embedding_jobs (document_id) VALUES (%s)", (doc_id,))

    statuses = conn.execute(
        "SELECT status FROM embedding_jobs WHERE document_id = %s ORDER BY id", (doc_id,)
    ).fetchall()

    assert [s[0] for s in statuses] == ["done", "pending"]


def test_pending_jobs_of_different_documents_do_not_collide(conn: psycopg.Connection):
    """코얼레싱 단위는 문서다 — 전역으로 pending 1건이 되면 큐가 성립하지 않는다."""
    insert_document(conn, content_hash="sha256:doc-1")
    insert_document(conn, content_hash="sha256:doc-2")

    (pending,) = conn.execute(
        "SELECT count(*) FROM embedding_jobs WHERE status = 'pending'"
    ).fetchone()

    assert pending == 2


def test_job_defaults_are_filled_in(conn: psycopg.Connection):
    """워커의 claim 쿼리가 `status='pending' AND next_attempt_at <= now()`로 고른다.

    두 기본값이 없으면 갓 생성된 잡이 집히지 않는다. 트리거도 `document_id` 하나만
    넣으므로(003_triggers.sql), 나머지 컬럼은 전부 이 기본값에 의존한다.
    """
    doc_id = insert_document(conn)

    status, attempts, claimable, last_error, started_at, finished_at = conn.execute(
        """
        SELECT status, attempts, next_attempt_at <= now(), last_error, started_at, finished_at
        FROM embedding_jobs WHERE document_id = %s
        """,
        (doc_id,),
    ).fetchone()

    assert status == "pending"
    assert attempts == 0
    assert claimable is True
    assert (last_error, started_at, finished_at) == (None, None, None)
