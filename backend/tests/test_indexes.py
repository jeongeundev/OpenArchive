"""벡터 인덱스 (ARCHITECTURE.md "벡터 인덱스", ADR-002·ADR-011).

ADR-002는 HNSW를 채택하면서 **"인덱스 생성 실행은 M0에서 확인한다 — 버전이 맞아도
빌드 옵션 등으로 막힐 가능성은 남는다"**를 미해소 항목으로 남겼다. 이 파일이 그
항목을 로컬 환경에 대해 해소한다.

`tdd-guard` 훅은 `*_indexes.sql`을 테스트 면제로 두지만, CLAUDE.md는 "인덱스가 검색
계획에 실제로 쓰이는지 확인이 필요하면 테스트를 직접 추가하라"고 한다. 인덱스의
존재가 아니라 **쓸 수 있음**이 검증 대상이므로 여기 테스트를 둔다.

> **로컬 통과가 실 클러스터 통과를 뜻하지 않는다.** 여기는 `pgvector/pgvector:pg17`
> 컨테이너이고, 실 배포판은 OpenSQL v3의 pgvector 0.8.1이다 (OPENSQL_RESEARCH.md §0).
> 실 검증은 VM 환경에서 별도로 수행한다.
"""

import psycopg
import pytest

INDEX_NAME = "idx_chunks_embedding"
EDGE_INDEXES = {
    "src": "idx_document_edges_src_kind",
    "dst": "idx_document_edges_dst_kind",
}

# ADR-011이 검색 트랜잭션에서 끌어올릴 파라미터. 기본값은 40이다.
EF_SEARCH_DEFAULT = "40"
EF_SEARCH_RAISED = "200"


def vec(*head: float) -> str:
    """앞자리만 지정하고 나머지를 0으로 채운 vector(1024) 리터럴.

    차원은 1024 고정이므로(ADR-003) 테스트에서도 줄이지 않는다.
    """
    values = [*head, *([0.0] * (1024 - len(head)))]
    return "[" + ",".join(str(v) for v in values) + "]"


@pytest.fixture
def conn(migrated_db: str):
    """autocommit 연결.

    거리 정렬·계획 확인은 명시적 트랜잭션(`conn.transaction()`)으로 감싸는데,
    이는 검색 쿼리가 plain `BEGIN … COMMIT` 안에서 도는 것과 같은 형태다 (ADR-010).
    """
    with psycopg.connect(migrated_db, autocommit=True) as c:
        yield c


def insert_document(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """
        INSERT INTO documents (title, content_type, content, content_hash, owner_id)
        VALUES ('벡터 인덱스 확인용 문서', 'md', '청크를 담기 위한 본문', 'sha256:idx', 'alice')
        RETURNING id
        """
    ).fetchone()
    return row[0]


def insert_chunk(conn: psycopg.Connection, doc_id: str, chunk_index: int, embedding: str) -> None:
    conn.execute(
        """
        INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
        VALUES (%s, 1, %s, %s, %s)
        """,
        (doc_id, chunk_index, f"청크 {chunk_index}", embedding),
    )


def test_embedding_index_is_built_with_the_hnsw_access_method(conn: psycopg.Connection):
    """정의 문자열이 아니라 **접근 방식**을 본다.

    `pg_indexes.indexdef`는 우리가 적어 넣은 SQL을 되돌려줄 뿐이라, 실제로 어떤
    인덱스가 만들어졌는지 답하지 못한다. `pg_am`을 조회해야 HNSW로 빌드됐음이
    확인된다 — ADR-002가 M0로 미뤄둔 바로 그 항목이다.
    """
    row = conn.execute(
        """
        SELECT x.indrelid::regclass::text, am.amname
        FROM pg_class i
        JOIN pg_am am ON am.oid = i.relam
        JOIN pg_index x ON x.indexrelid = i.oid
        WHERE i.relname = %s
        """,
        (INDEX_NAME,),
    ).fetchone()

    assert row == ("document_chunks", "hnsw")


def test_embedding_index_uses_the_cosine_distance_operator_class(conn: psycopg.Connection):
    """`vector_cosine_ops`여야 검색 쿼리의 `<=>` 정렬이 이 인덱스를 탄다.

    L2(`vector_l2_ops`)로 만들면 인덱스는 정상 생성되지만 `<=>` 쿼리가 조용히
    풀스캔으로 떨어진다. BGE-M3의 정규화 임베딩에 맞춘 선택이기도 하다 (ADR-002).
    """
    (opclass,) = conn.execute(
        """
        SELECT opc.opcname
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_opclass opc ON opc.oid = x.indclass[0]
        WHERE i.relname = %s
        """,
        (INDEX_NAME,),
    ).fetchone()

    assert opclass == "vector_cosine_ops"


def test_planner_can_use_the_hnsw_index_for_distance_ordering(conn: psycopg.Connection):
    """검색 쿼리 형태(`ORDER BY embedding <=> q LIMIT k`)가 인덱스를 탈 수 있는가.

    `enable_seqscan = off`를 거는 이유: 행이 적으면 플래너가 순차 스캔을 고르는 것이
    **정상**이며 인덱스 결함이 아니다. 여기서 확인하려는 것은 "인덱스를 쓸 수 있는가"
    이므로, 더 싼 대안만 닫아 두고 계획을 본다. 실제 검색 경로에서는 이 설정을 쓰지
    않는다 — 데이터가 쌓이면 플래너가 스스로 인덱스를 고른다.

    `SET LOCAL`이므로 트랜잭션 밖으로 새지 않는다.
    """
    doc_id = insert_document(conn)
    for i in range(50):
        insert_chunk(conn, doc_id, i, vec(1.0, i * 0.01))

    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                "EXPLAIN SELECT id FROM document_chunks ORDER BY embedding <=> %s LIMIT 5",
                (vec(1.0),),
            ).fetchall()
        )

    assert INDEX_NAME in plan, f"HNSW 인덱스를 타지 않았다:\n{plan}"
    assert "Index Scan" in plan, f"인덱스 스캔이 아니다:\n{plan}"


def test_ef_search_can_be_raised_for_a_single_transaction(conn: psycopg.Connection):
    """ADR-011이 검색에서 쓸 `SET LOCAL hnsw.ef_search = 200`의 가용성 확인.

    post-filter recall 완화를 이 파라미터에 의존하므로, 인덱스를 만든 자리에서
    함께 확인해 둔다. `SET LOCAL`이라 트랜잭션이 끝나면 자동 복원되는 것이 핵심이다 —
    복원되지 않으면 커넥션 풀에서 재사용되는 연결에 설정이 눌어붙는다.

    `hnsw.iterative_scan`은 여기서 켜지 않는다. pgvector 0.8+에서 쓸 수 있으나
    ADR-011 보강 3이 "실측 없이 켜지 않는다"고 정했고, 측정 대상인 필터 결합 검색
    쿼리가 아직 없다.
    """
    doc_id = insert_document(conn)
    insert_chunk(conn, doc_id, 0, vec(1.0))  # 이 벡터 연산으로 pgvector 모듈이 로드된다 (아래 테스트)

    (before,) = conn.execute("SHOW hnsw.ef_search").fetchone()

    with conn.transaction():
        conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH_RAISED}")
        (inside,) = conn.execute("SHOW hnsw.ef_search").fetchone()

    (after,) = conn.execute("SHOW hnsw.ef_search").fetchone()

    assert before == EF_SEARCH_DEFAULT
    assert inside == EF_SEARCH_RAISED
    assert after == EF_SEARCH_DEFAULT


def test_ef_search_applies_when_set_before_the_first_vector_operation(
    conn: psycopg.Connection, migrated_db: str
):
    """검색 트랜잭션의 **실제 순서**를 그대로 태운다 — `SET LOCAL`이 벡터 쿼리보다 앞선다.

    `hnsw.ef_search`는 pgvector 모듈이 세션에 로드된 뒤에야 등록된다. 그래서 갓
    맺은 연결에서 `SHOW hnsw.ef_search`는 `unrecognized configuration parameter`로
    실패한다(실측 확인). ARCHITECTURE의 검색 쿼리는 하필 그 순서다 — `BEGIN` 직후
    `SET LOCAL`을 걸고, 벡터 연산은 그 뒤에 온다.

    다행히 PostgreSQL은 모듈 로드 전의 값을 placeholder로 받아 두었다가 변수가
    정의되는 시점에 넘겨준다. 따라서 순서를 바꿀 필요가 없다. **이 테스트가 그
    전제를 고정한다** — 깨지면 검색이 조용히 기본값 40으로 돌아 recall이 떨어진다.
    """
    doc_id = insert_document(conn)
    insert_chunk(conn, doc_id, 0, vec(1.0))

    # 갓 맺은 연결이어야 한다. `conn`은 위 INSERT로 이미 모듈이 로드된 상태다.
    with psycopg.connect(migrated_db, autocommit=True) as fresh:
        with fresh.transaction():
            fresh.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH_RAISED}")
            fresh.execute(
                "SELECT id FROM document_chunks ORDER BY embedding <=> %s LIMIT 1", (vec(1.0),)
            )
            (inside,) = fresh.execute("SHOW hnsw.ef_search").fetchone()

        (after,) = fresh.execute("SHOW hnsw.ef_search").fetchone()

    assert inside == EF_SEARCH_RAISED
    assert after == EF_SEARCH_DEFAULT


def test_cosine_distance_orders_chunks_by_similarity(conn: psycopg.Connection):
    """연산자와 차원 설정이 의미대로 동작하는가 — 인덱스가 아니라 `<=>` 자체를 본다.

    질의 벡터를 `[1,0,0,…]`로 두면 코사인 거리는 다음과 같다.
        같은 방향 `[1,0,…]`      → 0
        45도 `[1,1,0,…]`         → 1 − 1/√2 ≈ 0.293
        직교 `[0,1,0,…]`         → 1
    이 순서가 깨지면 검색 결과 순위 전체가 무의미해진다.
    """
    doc_id = insert_document(conn)
    insert_chunk(conn, doc_id, 0, vec(1.0))  # 같은 방향
    insert_chunk(conn, doc_id, 1, vec(1.0, 1.0))  # 45도
    insert_chunk(conn, doc_id, 2, vec(0.0, 1.0))  # 직교

    with conn.transaction():
        rows = conn.execute(
            """
            SELECT chunk_index, embedding <=> %s AS dist
            FROM document_chunks
            ORDER BY embedding <=> %s
            """,
            (vec(1.0), vec(1.0)),
        ).fetchall()

    assert [r[0] for r in rows] == [0, 1, 2]
    assert rows[0][1] == pytest.approx(0.0, abs=1e-6)
    assert rows[1][1] == pytest.approx(1 - 1 / 2**0.5, abs=1e-6)
    assert rows[2][1] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("side", ["src", "dst"])
def test_planner_can_use_each_document_edge_traversal_index(
    conn: psycopg.Connection, side: str
):
    column = f"{side}_document_id"
    index_name = EDGE_INDEXES[side]

    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                f"EXPLAIN SELECT kind FROM document_edges WHERE {column} = %s AND kind = %s",
                ("00000000-0000-0000-0000-000000000001", "related"),
            ).fetchall()
        )

    assert index_name in plan, f"순회 인덱스를 타지 않았다:\n{plan}"
    assert "Index" in plan, f"인덱스 계획이 아니다:\n{plan}"
