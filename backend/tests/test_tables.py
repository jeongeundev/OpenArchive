"""스키마 제약 (ARCHITECTURE.md "DB 스키마", ADR-001·ADR-003).

여기서 검증하는 것은 테이블의 "존재"가 아니라 **제약이 실제로 막아주는가**다.
빈 본문 차단·벡터 차원 고정·CASCADE·코얼레싱은 전부 DB가 판정하므로 Mock으로는
확인할 수 없다. 실제 pgvector 컨테이너에 `backend/openarchive/migrations/`를 적용한
`migrated_db` 픽스처 위에서 돈다.

문서를 INSERT하면 003_triggers.sql의 트리거가 v1 이력과 pending 잡을 함께 만든다.
여기서는 그것을 "이미 존재하는 행"으로만 다루고, 트리거의 동작 자체는 검증하지
않는다 — 그쪽은 test_triggers.py의 몫이다.
`embedding_jobs` 직접 INSERT 금지 규칙(CLAUDE.md)은 애플리케이션 코드를 대상으로
하며, 제약 자체가 검증 대상인 여기서는 직접 INSERT가 유일한 수단이다.
"""

import hashlib

import psycopg
import pytest

CORE_TABLES = {
    "documents",
    "document_versions",
    "document_chunks",
    "embedding_jobs",
    "document_edges",
    "document_links",
    "users",
    "sessions",
    "api_tokens",
    "shares",
    "document_files",
    "idempotency_keys",
    "groups",
    "group_members",
    "document_grants",
}

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


def test_a_job_kind_outside_the_known_values_is_rejected(conn: psycopg.Connection):
    """잡 종류는 `embed`·`edges`·`extract` 셋뿐이다 (016, 021).

    워커는 `kind`로 처리 본체를 가른다. 제약이 없으면 오타 하나가 어느 분기에도
    걸리지 않는 잡을 만들고, 그 잡은 claim은 되지만 아무 일도 하지 않은 채 영원히
    큐에 남는다 — 관계 미반영 카운터도 그만큼 0으로 돌아오지 않는다.
    """
    doc_id = insert_document(conn)
    conn.execute("UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (doc_id,))

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        conn.execute(
            "INSERT INTO embedding_jobs (document_id, kind) VALUES (%s, 'edge')", (doc_id,)
        )

    assert "embedding_jobs_kind_valid" in str(exc.value)


def test_the_coalescing_key_includes_the_job_kind(conn: psycopg.Connection):
    """코얼레싱 단위는 (문서, 종류)다 (016).

    종류가 키에 없으면 ready 전이의 관계 잡 INSERT가 같은 문서의 pending 임베딩 잡과
    충돌해 `ON CONFLICT DO NOTHING`으로 **조용히 사라진다.** 그러면 그 문서의 관계는
    아무도 계산하지 않는다. 같은 종류 안에서는 여전히 1건으로 접힌다.
    """
    doc_id = insert_document(conn)  # 트리거가 pending 임베딩 잡 1건을 만든다
    conn.execute("INSERT INTO embedding_jobs (document_id, kind) VALUES (%s, 'edges')", (doc_id,))

    kinds = conn.execute(
        "SELECT kind FROM embedding_jobs WHERE document_id = %s AND status = 'pending' ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [k[0] for k in kinds] == ["embed", "edges"]

    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        conn.execute(
            "INSERT INTO embedding_jobs (document_id, kind) VALUES (%s, 'edges')", (doc_id,)
        )

    assert "uq_pending_job_per_doc_kind" in str(exc.value)


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


def test_auth_table_columns_and_constraints_match_the_account_model(
    conn: psycopg.Connection,
):
    rows = conn.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name IN ('users', 'sessions')
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()

    assert rows == [
        ("sessions", "token", "text", "NO", None),
        ("sessions", "user_id", "uuid", "NO", None),
        ("sessions", "created_at", "timestamp with time zone", "NO", "now()"),
        ("sessions", "expires_at", "timestamp with time zone", "NO", None),
        ("users", "id", "uuid", "NO", "gen_random_uuid()"),
        ("users", "username", "text", "NO", None),
        ("users", "password_hash", "text", "NO", None),
        ("users", "is_admin", "boolean", "NO", "false"),
        ("users", "created_at", "timestamp with time zone", "NO", "now()"),
    ]

    constraints = conn.execute(
        """
        SELECT tc.table_name, tc.constraint_type,
               array_agg(kcu.column_name::text ORDER BY kcu.ordinal_position)
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
        WHERE tc.table_schema = 'public'
          AND tc.table_name IN ('users', 'sessions')
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
        GROUP BY tc.table_name, tc.constraint_name, tc.constraint_type
        ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name
        """
    ).fetchall()

    assert constraints == [
        ("sessions", "FOREIGN KEY", ["user_id"]),
        ("sessions", "PRIMARY KEY", ["token"]),
        ("users", "PRIMARY KEY", ["id"]),
        ("users", "UNIQUE", ["username"]),
    ]


def test_username_is_unique(conn: psycopg.Connection):
    insert_user = "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash')"
    conn.execute(insert_user, ("alice",))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert_user, ("alice",))


def test_deleting_a_user_cascades_to_sessions(conn: psycopg.Connection):
    (user_id,) = conn.execute(
        """
        INSERT INTO users (username, password_hash)
        VALUES ('alice', 'scrypt-hash')
        RETURNING id
        """
    ).fetchone()
    conn.execute(
        """
        INSERT INTO sessions (token, user_id, expires_at)
        VALUES ('session-token', %s, now() + interval '1 hour')
        """,
        (user_id,),
    )

    conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM sessions").fetchone()
    assert remaining == 0


def test_api_token_columns_and_constraints_match_the_delegated_token_model(
    conn: psycopg.Connection,
):
    rows = conn.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'api_tokens'
        ORDER BY ordinal_position
        """
    ).fetchall()

    assert rows == [
        ("id", "uuid", "NO", "gen_random_uuid()"),
        ("user_id", "uuid", "YES", None),
        ("name", "text", "NO", None),
        ("token_hash", "text", "NO", None),
        ("scope", "text", "NO", None),
        ("created_at", "timestamp with time zone", "NO", "now()"),
        ("share_id", "uuid", "YES", None),
    ]

    constraints = conn.execute(
        """
        SELECT tc.constraint_type,
               array_agg(kcu.column_name::text ORDER BY kcu.ordinal_position)
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
        WHERE tc.table_schema = 'public'
          AND tc.table_name = 'api_tokens'
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
        GROUP BY tc.constraint_name, tc.constraint_type
        ORDER BY tc.constraint_type, tc.constraint_name
        """
    ).fetchall()

    assert constraints == [
        ("FOREIGN KEY", ["share_id"]),
        ("FOREIGN KEY", ["user_id"]),
        ("PRIMARY KEY", ["id"]),
        ("UNIQUE", ["token_hash"]),
    ]


@pytest.mark.parametrize("scope", ["read", "read_write"])
def test_api_token_accepts_supported_scopes(conn: psycopg.Connection, scope: str):
    (user_id,) = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash') RETURNING id",
        (f"user-{scope}",),
    ).fetchone()

    conn.execute(
        "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'ci', %s, %s)",
        (user_id, f"sha256:{scope}", scope),
    )


@pytest.mark.parametrize("scope", ["write", "admin", ""])
def test_api_token_rejects_unsupported_scopes(conn: psycopg.Connection, scope: str):
    (user_id,) = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash') RETURNING id",
        (f"user-{scope or 'empty'}",),
    ).fetchone()

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'ci', %s, %s)",
            (user_id, f"sha256:{scope}", scope),
        )


def test_api_token_hash_is_globally_unique(conn: psycopg.Connection):
    user_ids = conn.execute(
        """
        INSERT INTO users (username, password_hash)
        VALUES ('alice', 'scrypt-hash'), ('bob', 'scrypt-hash')
        RETURNING id
        """
    ).fetchall()
    insert_token = """
        INSERT INTO api_tokens (user_id, name, token_hash, scope)
        VALUES (%s, %s, 'same-sha256-hash', 'read')
    """
    conn.execute(insert_token, (user_ids[0][0], "alice-token"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert_token, (user_ids[1][0], "bob-token"))


def test_deleting_a_user_cascades_to_api_tokens(conn: psycopg.Connection):
    (user_id,) = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('alice', 'scrypt-hash') RETURNING id"
    ).fetchone()
    conn.execute(
        """
        INSERT INTO api_tokens (user_id, name, token_hash, scope)
        VALUES (%s, 'ci-ingest', 'sha256:token', 'read_write')
        """,
        (user_id,),
    )

    conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM api_tokens").fetchone()
    assert remaining == 0


def test_deleting_a_document_does_not_delete_api_tokens(conn: psycopg.Connection):
    (user_id,) = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('alice', 'scrypt-hash') RETURNING id"
    ).fetchone()
    conn.execute(
        """
        INSERT INTO api_tokens (user_id, name, token_hash, scope)
        VALUES (%s, 'ci-ingest', 'sha256:token', 'read_write')
        """,
        (user_id,),
    )
    document_id = insert_document(conn)

    conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM api_tokens").fetchone()
    assert remaining == 1


def test_document_content_over_500kb_is_rejected(conn: psycopg.Connection):
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        insert_document(conn, content="가" * 500_001)

    assert "documents_content_length" in str(exc.value)


def test_document_links_store_titles_without_resolving_target_ids(
    conn: psycopg.Connection,
):
    rows = conn.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'document_links'
        ORDER BY ordinal_position
        """
    ).fetchall()

    assert rows == [
        ("src_document_id", "uuid", "NO"),
        ("src_chunk_index", "integer", "YES"),
        ("target_title", "text", "NO"),
    ]


def insert_edge(
    conn: psycopg.Connection,
    src_id: str,
    dst_id: str,
    *,
    kind: str = "related",
    src_chunk_index: int | None = None,
    dst_chunk_index: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, %s, %s, %s, 0.75)
        """,
        (src_id, dst_id, kind, src_chunk_index, dst_chunk_index),
    )


def test_document_edges_columns_match_the_document_node_model(conn: psycopg.Connection):
    rows = conn.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'document_edges'
        ORDER BY ordinal_position
        """
    ).fetchall()

    assert rows == [
        ("src_document_id", "uuid", "NO"),
        ("dst_document_id", "uuid", "NO"),
        ("kind", "text", "NO"),
        ("src_chunk_index", "integer", "YES"),
        ("dst_chunk_index", "integer", "YES"),
        ("score", "real", "NO"),
    ]


def test_document_edge_rejects_a_self_reference(conn: psycopg.Connection):
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_edge(conn, doc_id, doc_id)


def test_document_edge_rejects_an_unknown_kind(conn: psycopg.Connection):
    src_id = insert_document(conn, content_hash="sha256:edge-kind-src")
    dst_id = insert_document(conn, content_hash="sha256:edge-kind-dst")

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_edge(conn, src_id, dst_id, kind="follows")


@pytest.mark.parametrize("chunk_pair", [(None, None), (2, 7)])
def test_document_edge_rejects_a_duplicate_chunk_pair(
    conn: psycopg.Connection, chunk_pair: tuple[int | None, int | None]
):
    src_id = insert_document(conn, content_hash=f"sha256:edge-dup-src:{chunk_pair}")
    dst_id = insert_document(conn, content_hash=f"sha256:edge-dup-dst:{chunk_pair}")
    src_chunk_index, dst_chunk_index = chunk_pair

    insert_edge(
        conn,
        src_id,
        dst_id,
        src_chunk_index=src_chunk_index,
        dst_chunk_index=dst_chunk_index,
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_edge(
            conn,
            src_id,
            dst_id,
            src_chunk_index=src_chunk_index,
            dst_chunk_index=dst_chunk_index,
        )


@pytest.mark.parametrize("deleted_side", ["src", "dst"])
def test_deleting_either_document_cascades_to_edges(
    conn: psycopg.Connection, deleted_side: str
):
    src_id = insert_document(conn, content_hash=f"sha256:edge-cascade-src:{deleted_side}")
    dst_id = insert_document(conn, content_hash=f"sha256:edge-cascade-dst:{deleted_side}")
    insert_edge(conn, src_id, dst_id)

    deleted_id = src_id if deleted_side == "src" else dst_id
    conn.execute("DELETE FROM documents WHERE id = %s", (deleted_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM document_edges").fetchone()
    assert remaining == 0


# --- document_files: 원본 파일의 판 (018_files_tables.sql, #108) -----------------------

ORIGINAL_BYTES = b"%PDF-1.7\n\x00\x01\xff original bytes"


def insert_file(
    conn: psycopg.Connection,
    document_id: str,
    file_version: int = 1,
    data: bytes = ORIGINAL_BYTES,
    text_version: int = 1,
    filename: str = "report.pdf",
) -> None:
    """원본 한 판을 넣는다. 바이트는 업로드 서비스와 같이 %b(바이너리)로 보낸다."""
    conn.execute(
        """
        INSERT INTO document_files
          (document_id, file_version, filename, data, text_version, uploaded_by)
        VALUES (%s, %s, %s, %b, %s, 'alice')
        """,
        (document_id, file_version, filename, data, text_version),
    )


def test_document_file_size_and_sha256_are_computed_by_the_database(
    conn: psycopg.Connection,
):
    """저장된 바이트와 크기·해시가 어긋날 수 없다 — 둘 다 DB가 data에서 계산한다."""
    doc_id = insert_document(conn)
    insert_file(conn, doc_id)

    size, sha256, data = conn.execute(
        "SELECT size, sha256, data FROM document_files WHERE document_id = %s",
        (doc_id,),
    ).fetchone()

    assert bytes(data) == ORIGINAL_BYTES
    assert size == len(ORIGINAL_BYTES)
    assert sha256 == hashlib.sha256(ORIGINAL_BYTES).hexdigest()


@pytest.mark.parametrize(
    ("column", "value"), [("size", 1), ("sha256", "0" * 64)]
)
def test_document_file_generated_columns_cannot_be_written(
    conn: psycopg.Connection, column: str, value: object
):
    doc_id = insert_document(conn, content_hash=f"sha256:file-gen:{column}")

    with pytest.raises(psycopg.errors.GeneratedAlways):
        conn.execute(
            f"""
            INSERT INTO document_files
              (document_id, file_version, filename, data, text_version, uploaded_by,
               {column})
            VALUES (%s, 1, 'report.pdf', %b, 1, 'alice', %s)
            """,
            (doc_id, ORIGINAL_BYTES, value),
        )


def test_document_files_keep_every_version_per_document(conn: psycopg.Connection):
    """교체는 이전 판을 덮지 않고 새 판을 쌓는다. 같은 판 번호는 한 번뿐이다."""
    doc_id = insert_document(conn)
    insert_file(conn, doc_id, file_version=1, data=b"first original")
    insert_file(conn, doc_id, file_version=2, data=b"second original")

    rows = conn.execute(
        "SELECT file_version, data FROM document_files WHERE document_id = %s"
        " ORDER BY file_version",
        (doc_id,),
    ).fetchall()
    assert [(v, bytes(d)) for v, d in rows] == [
        (1, b"first original"),
        (2, b"second original"),
    ]

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_file(conn, doc_id, file_version=2, data=b"overwrite attempt")


def test_document_file_requires_an_existing_text_version(conn: psycopg.Connection):
    """원본이 가리키는 텍스트 버전은 실재해야 한다 — v1은 INSERT 트리거가 만든다."""
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_file(conn, doc_id, text_version=2)

    insert_file(conn, doc_id, text_version=1)
    (count,) = conn.execute(
        "SELECT count(*) FROM document_files WHERE document_id = %s", (doc_id,)
    ).fetchone()
    assert count == 1


def test_deleting_a_text_version_does_not_silently_delete_original_files(
    conn: psycopg.Connection,
):
    """원본 판은 텍스트 버전을 지운다고 따라 지워지지 않는다 — 판 이력은 append-only다.

    FK가 CASCADE면 이후 텍스트 버전 정리가 원본을 조용히 지워, 판으로 막으려던 유실이
    다시 생긴다. 원본을 지우는 경로는 문서 삭제 하나다.
    """
    doc_id = insert_document(conn)
    insert_file(conn, doc_id, text_version=1)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "DELETE FROM document_versions WHERE document_id = %s AND version = 1",
            (doc_id,),
        )


def test_deleting_a_document_deletes_its_original_files(conn: psycopg.Connection):
    doc_id = insert_document(conn)
    insert_file(conn, doc_id, file_version=1)
    insert_file(conn, doc_id, file_version=2)

    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM document_files").fetchone()
    assert remaining == 0


def test_empty_original_file_is_rejected(conn: psycopg.Connection):
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_file(conn, doc_id, data=b"")


def test_file_version_starts_at_one(conn: psycopg.Connection):
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_file(conn, doc_id, file_version=0)


def insert_idempotency_key(
    conn: psycopg.Connection,
    document_id: str,
    owner_id: str = "alice",
    key: str = "upload-1",
) -> None:
    conn.execute(
        """
        INSERT INTO idempotency_keys (owner_id, key, request_hash, document_id)
        VALUES (%s, %s, 'sha256:request', %s)
        """,
        (owner_id, key, document_id),
    )


def test_an_idempotency_key_is_unique_per_owner(conn: psycopg.Connection):
    """같은 소유자의 같은 키는 한 번뿐이다 — 동시 재시도를 기본키가 직렬화한다 (ADR-047).

    다른 소유자의 같은 키는 충돌하지 않는다. 키는 클라이언트가 고르므로 소유자 범위가
    아니면 남의 키와 부딪혀 그 존재를 알게 된다.
    """
    doc_id = insert_document(conn)
    insert_idempotency_key(conn, doc_id, owner_id="alice")
    insert_idempotency_key(conn, doc_id, owner_id="bob")

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_idempotency_key(conn, doc_id, owner_id="alice")


def test_an_idempotency_key_records_when_it_was_created(conn: psycopg.Connection):
    """만료 정리(24시간)의 기준 시각이 DB에서 채워진다."""
    doc_id = insert_document(conn)
    insert_idempotency_key(conn, doc_id)

    (age_is_fresh,) = conn.execute(
        "SELECT now() - created_at < interval '1 minute' FROM idempotency_keys"
    ).fetchone()
    assert age_is_fresh is True


@pytest.mark.parametrize("key", ["", "k" * 256])
def test_an_idempotency_key_must_be_1_to_255_characters(
    conn: psycopg.Connection, key: str
):
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_idempotency_key(conn, doc_id, key=key)


def test_an_idempotency_key_must_point_to_an_existing_document(conn: psycopg.Connection):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_idempotency_key(conn, "00000000-0000-0000-0000-000000000000")


def test_deleting_a_document_deletes_its_idempotency_key(conn: psycopg.Connection):
    """키와 문서는 함께 있거나 함께 없다 (ADR-047 결정 2) — 지운 문서를 가리키는 키가
    남으면 재시도에 돌려줄 문서가 없다."""
    doc_id = insert_document(conn)
    insert_idempotency_key(conn, doc_id)

    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM idempotency_keys").fetchone()
    assert remaining == 0


# --- 잡 lease (020, ADR-050) -------------------------------------------------------


def test_a_processing_job_must_carry_a_lease(conn: psycopg.Connection):
    """`processing`은 lease 없이 있을 수 없다 (ADR-050 결정 1).

    좀비 판정이 `lease_expires_at < now()`이므로 lease가 NULL인 processing 잡은 **영원히
    회수되지 않는다** — 에러 없이 문서 하나가 processing에 멈춘다. lease를 찍지 않는 옛
    워커가 배포 중에 남아 있으면 바로 그 행을 만든다. 제약이 그것을 조용한 멈춤 대신
    선점 실패로 바꾼다.
    """
    doc_id = insert_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        conn.execute(
            "UPDATE embedding_jobs SET status = 'processing' WHERE document_id = %s", (doc_id,)
        )

    assert "embedding_jobs_processing_has_lease" in str(exc.value)


def test_a_job_leaves_its_lease_behind_when_it_stops_processing(conn: psycopg.Connection):
    """lease는 processing에서만 요구된다 — 반납·실패·마감이 lease를 지울 필요는 없다."""
    doc_id = insert_document(conn)
    conn.execute(
        "UPDATE embedding_jobs SET status = 'processing', lease_expires_at = now()"
        " WHERE document_id = %s",
        (doc_id,),
    )

    for status in ("pending", "done", "error"):
        conn.execute(
            "UPDATE embedding_jobs SET status = %s WHERE document_id = %s", (status, doc_id)
        )


async def test_the_lease_migration_keeps_the_old_deadline_for_jobs_in_flight(
    clean_db: str, tmp_path
):
    """이행 순간 이미 processing인 잡은 옛 판정과 같은 시각(`started_at + 5분`)에 회수된다.

    NULL로 두면 위 제약에 걸려 마이그레이션이 실패하고, `now()`로 두면 배포 직후 스윕이
    아직 살아 있을 수 있는 잡을 즉시 회수한다. 옛 임계를 그대로 옮기는 것만이 이행 전후로
    판정을 바꾸지 않는다.
    """
    import shutil

    from openarchive.migrations import MIGRATIONS_DIR, run_migrations

    before = tmp_path / "before"
    before.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name < "020":
            shutil.copy(path, before / path.name)
    await run_migrations(clean_db, before)

    with psycopg.connect(clean_db, autocommit=True) as c:
        doc_id = insert_document(c)
        c.execute(
            "UPDATE embedding_jobs SET status = 'processing',"
            " started_at = now() - interval '2 minutes' WHERE document_id = %s",
            (doc_id,),
        )

    await run_migrations(clean_db)

    with psycopg.connect(clean_db, autocommit=True) as c:
        (deadline_matches,) = c.execute(
            "SELECT lease_expires_at = started_at + interval '5 minutes'"
            " FROM embedding_jobs WHERE document_id = %s",
            (doc_id,),
        ).fetchone()
    assert deadline_matches is True


# --- 추출 상태와 조건부 빈 본문 제약 (021, ADR-052) ---------------------------------


def insert_extracting_document(
    conn: psycopg.Connection,
    status: str = "pending",
    content: str = "",
    content_hash: str = "sha256:extracting",
) -> str:
    """OCR 대상 업로드처럼 추출 상태를 지정해 한 건 넣는다."""
    row = conn.execute(
        """
        INSERT INTO documents
          (title, content_type, content, content_hash, owner_id, extraction_status)
        VALUES ('스캔 문서', 'pdf', %s, %s, 'alice', %s)
        RETURNING id
        """,
        (content, content_hash, status),
    ).fetchone()
    return row[0]


def test_extraction_status_defaults_to_done(conn: psycopg.Connection):
    """기존 경로(텍스트가 있는 업로드·텍스트 공급)는 추출이 끝난 문서로 들어간다."""
    doc_id = insert_document(conn)

    (status,) = conn.execute(
        "SELECT extraction_status FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    assert status == "done"


def test_an_unknown_extraction_status_is_rejected(conn: psycopg.Connection):
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        insert_extracting_document(conn, status="running", content="본문")

    assert "documents_extraction_status_valid" in str(exc.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n  \n", "\f\r\n"])
def test_a_done_document_still_rejects_blank_content(conn: psycopg.Connection, blank: str):
    """추출이 끝났다고 표시된 문서의 빈 텍스트는 여전히 DB가 막는다 (ADR-052 결정 5)."""
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        insert_extracting_document(conn, status="done", content=blank)

    assert "documents_content_not_blank" in str(exc.value)


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_a_document_still_extracting_or_failed_may_have_blank_content(
    conn: psycopg.Connection, status: str
):
    """「추출 중」 문서 행은 빈 텍스트로 먼저 생긴다. 인식에 실패한 새 문서도 빈 채로 남는다."""
    doc_id = insert_extracting_document(conn, status=status)

    (content,) = conn.execute(
        "SELECT content FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    assert content == ""


def test_marking_a_blank_document_done_without_content_is_rejected(conn: psycopg.Connection):
    """추출 완료 표시와 본문 채우기는 한 문장에서 함께 일어나야 한다."""
    doc_id = insert_extracting_document(conn)

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        conn.execute(
            "UPDATE documents SET extraction_status = 'done' WHERE id = %s", (doc_id,)
        )

    assert "documents_content_not_blank" in str(exc.value)


def test_an_original_file_may_wait_for_its_text_version(conn: psycopg.Connection):
    """text_version NULL = 이 판의 텍스트가 아직 추출되지 않았다. 값이 있으면 FK가 여전히 건다."""
    doc_id = insert_extracting_document(conn)
    conn.execute(
        """
        INSERT INTO document_files
          (document_id, file_version, filename, data, text_version, uploaded_by)
        VALUES (%s, 1, 'scan.pdf', %b, NULL, 'alice')
        """,
        (doc_id, ORIGINAL_BYTES),
    )

    (text_version,) = conn.execute(
        "SELECT text_version FROM document_files WHERE document_id = %s", (doc_id,)
    ).fetchone()
    assert text_version is None

    # 추출 중 문서에는 텍스트 버전이 아직 없다 — 없는 버전을 가리킬 수는 없다.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert_file(conn, doc_id, file_version=2, text_version=1)


def test_extract_is_a_known_job_kind_and_coalesces_per_document(conn: psycopg.Connection):
    doc_id = insert_document(conn)  # pending 임베딩 잡 1건
    conn.execute(
        "INSERT INTO embedding_jobs (document_id, kind) VALUES (%s, 'extract')", (doc_id,)
    )

    kinds = conn.execute(
        "SELECT kind FROM embedding_jobs WHERE document_id = %s AND status = 'pending' ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [k[0] for k in kinds] == ["embed", "extract"]

    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        conn.execute(
            "INSERT INTO embedding_jobs (document_id, kind) VALUES (%s, 'extract')", (doc_id,)
        )

    assert "uq_pending_job_per_doc_kind" in str(exc.value)


# ── 열람 부여 (ADR-044, #97) ─────────────────────────────────────────────


def insert_user(conn: psycopg.Connection, username: str) -> str:
    (user_id,) = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'scrypt-hash') RETURNING id",
        (username,),
    ).fetchone()
    return user_id


def insert_group(conn: psycopg.Connection, name: str = "인사팀") -> str:
    (group_id,) = conn.execute(
        "INSERT INTO groups (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    return group_id


def grant_count(conn: psycopg.Connection) -> int:
    (count,) = conn.execute("SELECT count(*) FROM document_grants").fetchone()
    return count


def test_group_name_is_unique(conn: psycopg.Connection):
    insert_group(conn, "인사팀")

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_group(conn, "인사팀")


def test_a_user_is_a_member_of_a_group_at_most_once(conn: psycopg.Connection):
    group_id = insert_group(conn)
    user_id = insert_user(conn, "carol")
    add = "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)"
    conn.execute(add, (group_id, user_id))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(add, (group_id, user_id))


@pytest.mark.parametrize("deleted", ["group", "user"])
def test_deleting_a_group_or_a_user_removes_the_membership(
    conn: psycopg.Connection, deleted: str
):
    group_id = insert_group(conn)
    user_id = insert_user(conn, "carol")
    conn.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)", (group_id, user_id)
    )

    if deleted == "group":
        conn.execute("DELETE FROM groups WHERE id = %s", (group_id,))
    else:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    (remaining,) = conn.execute("SELECT count(*) FROM group_members").fetchone()
    assert remaining == 0


@pytest.mark.parametrize("grantees", [(), ("user", "group"), ("user", "share"),
                                      ("group", "share"), ("user", "group", "share")])
def test_a_grant_names_exactly_one_grantee(conn: psycopg.Connection, grantees: tuple):
    """세 종류 중 정확히 하나만 부여 대상이어야 한다."""
    doc_id = insert_document(conn)
    user_id = insert_user(conn, "carol") if "user" in grantees else None
    group_id = insert_group(conn) if "group" in grantees else None
    share_id = insert_share(conn, insert_user(conn, "owner")) if "share" in grantees else None

    with pytest.raises(psycopg.errors.CheckViolation) as error:
        conn.execute(
            "INSERT INTO document_grants (document_id, user_id, group_id, share_id) "
            "VALUES (%s, %s, %s, %s)",
            (doc_id, user_id, group_id, share_id),
        )
    assert error.value.diag.constraint_name == "document_grants_one_grantee"


@pytest.mark.parametrize("grantee", ["user_id", "group_id"])
def test_the_same_grant_cannot_be_stored_twice(conn: psycopg.Connection, grantee: str):
    doc_id = insert_document(conn)
    target = insert_user(conn, "carol") if grantee == "user_id" else insert_group(conn)
    grant = f"INSERT INTO document_grants (document_id, {grantee}) VALUES (%s, %s)"
    conn.execute(grant, (doc_id, target))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(grant, (doc_id, target))


@pytest.mark.parametrize("deleted", ["document", "user", "group"])
def test_deleting_the_document_or_the_grantee_removes_the_grant(
    conn: psycopg.Connection, deleted: str
):
    """부여는 문서와 대상 양쪽에 매달린다 — 어느 쪽이 사라져도 고아 부여가 남지 않는다."""
    doc_id = insert_document(conn)
    user_id = insert_user(conn, "carol")
    group_id = insert_group(conn)
    conn.execute(
        "INSERT INTO document_grants (document_id, user_id) VALUES (%s, %s)", (doc_id, user_id)
    )
    conn.execute(
        "INSERT INTO document_grants (document_id, group_id) VALUES (%s, %s)", (doc_id, group_id)
    )

    if deleted == "document":
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        assert grant_count(conn) == 0
    elif deleted == "user":
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        assert grant_count(conn) == 1
    else:
        conn.execute("DELETE FROM groups WHERE id = %s", (group_id,))
        assert grant_count(conn) == 1


def insert_share(conn: psycopg.Connection, owner_id: str, name: str = "협업") -> str:
    (share_id,) = conn.execute(
        "INSERT INTO shares (owner_user_id, name) VALUES (%s, %s) RETURNING id",
        (owner_id, name),
    ).fetchone()
    return share_id


def test_share_name_is_unique_within_its_owner(conn: psycopg.Connection):
    owner = insert_user(conn, "owner")
    first = insert_share(conn, owner)
    other = insert_share(conn, insert_user(conn, "other"))
    assert first != other
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_share(conn, owner)


def test_deleting_the_owner_removes_the_share(conn: psycopg.Connection):
    owner = insert_user(conn, "owner")
    insert_share(conn, owner)
    conn.execute("DELETE FROM users WHERE id = %s", (owner,))
    assert conn.execute("SELECT count(*) FROM shares").fetchone()[0] == 0


def test_the_same_share_grant_cannot_be_stored_twice(conn: psycopg.Connection):
    doc = insert_document(conn)
    share = insert_share(conn, insert_user(conn, "owner"))
    sql = "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)"
    conn.execute(sql, (doc, share))
    assert grant_count(conn) == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(sql, (doc, share))


@pytest.mark.parametrize("deleted", ["document", "share"])
def test_deleting_document_or_share_removes_share_grant(conn: psycopg.Connection, deleted: str):
    doc = insert_document(conn)
    share = insert_share(conn, insert_user(conn, "owner"))
    conn.execute("INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
                 (doc, share))
    if deleted == "document":
        conn.execute("DELETE FROM documents WHERE id = %s", (doc,))
    else:
        conn.execute("DELETE FROM shares WHERE id = %s", (share,))
    assert grant_count(conn) == 0


def test_share_read_token_is_removed_with_the_share(conn: psycopg.Connection):
    share = insert_share(conn, insert_user(conn, "owner"))
    conn.execute("INSERT INTO api_tokens (share_id, name, token_hash, scope) "
                 "VALUES (%s, 'partner', 'share-token', 'read')", (share,))
    assert conn.execute("SELECT user_id, share_id, scope FROM api_tokens").fetchone() == (
        None, share, "read"
    )
    conn.execute("DELETE FROM shares WHERE id = %s", (share,))
    assert conn.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0


@pytest.mark.parametrize("principal", ["none", "both"])
def test_api_token_names_exactly_one_principal(conn: psycopg.Connection, principal: str):
    owner = insert_user(conn, "owner")
    share = insert_share(conn, owner)
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        conn.execute("INSERT INTO api_tokens (user_id, share_id, name, token_hash, scope) "
                     "VALUES (%s, %s, 'partner', 'share-token', 'read')",
                     (owner if principal == "both" else None,
                      share if principal == "both" else None))
    assert error.value.diag.constraint_name == "api_tokens_one_principal"


def test_share_token_cannot_have_write_scope(conn: psycopg.Connection):
    share = insert_share(conn, insert_user(conn, "owner"))
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        conn.execute("INSERT INTO api_tokens (share_id, name, token_hash, scope) "
                     "VALUES (%s, 'partner', 'share-token', 'read_write')", (share,))
    assert error.value.diag.constraint_name == "api_tokens_share_read_only"


def test_username_cannot_start_with_share_principal_prefix(conn: psycopg.Connection):
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        insert_user(conn, "share:x")
    assert error.value.diag.constraint_name == "users_reserved_share_prefix"


@pytest.mark.parametrize("username", ["shared", "myshare:x"])
def test_username_allows_share_text_outside_reserved_prefix(conn: psycopg.Connection, username: str):
    user = insert_user(conn, username)
    assert conn.execute("SELECT username FROM users WHERE id = %s", (user,)).fetchone() == (username,)
