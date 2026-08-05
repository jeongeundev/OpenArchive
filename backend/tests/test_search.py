from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.search import (
    CANDIDATE_MULTIPLIER,
    EF_SEARCH,
    MAX_K,
    SEARCH_SQL,
    search_documents,
)


@pytest.fixture
async def worker_conn(migrated_db: str):
    """워커의 claim이 즉시 커밋되도록 autocommit 연결을 쓴다."""
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def search_conn(migrated_db: str):
    """검색 서비스가 자체 plain 트랜잭션을 열 수 있는 기본 연결이다."""
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


async def test_matching_document_is_ranked_first(worker_conn, search_conn):
    """SQL의 거리순 정렬을 검증한다. 검색 품질은 BGE-M3의 성질이라 FakeProvider로 검증하지 않는다."""
    provider = FakeProvider()
    matching_id = await insert_test_document(
        worker_conn, title="정합성 규정", content="OpenSQL 정합성 트리거 운영 규정"
    )
    await insert_test_document(
        worker_conn, title="휴가 안내", content="연차 휴가 신청 승인 안내"
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert hits[0].document_id == matching_id


async def test_private_document_is_hidden_from_another_user(worker_conn, search_conn):
    provider = FakeProvider()
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="기밀 접근통제 규정",
        owner_id="alice",
        visibility="private",
    )
    await insert_test_document(worker_conn, title="공개 안내", content="기밀 접근통제 안내")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="기밀 접근통제", user_id="bob")

    assert private_id not in {hit.document_id for hit in hits}


async def test_owner_can_search_own_private_document(worker_conn, search_conn):
    provider = FakeProvider()
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="기밀 접근통제 규정",
        owner_id="alice",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="기밀 접근통제", user_id="alice")

    assert private_id in {hit.document_id for hit in hits}


async def test_anonymous_search_returns_only_public_documents(worker_conn, search_conn):
    provider = FakeProvider()
    public_id = await insert_test_document(
        worker_conn, title="공개 규정", content="접근통제 공개 규정"
    )
    await insert_test_document(
        worker_conn,
        title="비공개 규정",
        content="접근통제 비공개 규정",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="접근통제")

    assert [hit.document_id for hit in hits] == [public_id]


async def test_tag_filter_uses_array_overlap(worker_conn, search_conn):
    provider = FakeProvider()
    tagged_id = await insert_test_document(
        worker_conn, title="규정", content="보안 점검 규정", tags=["규정", "보안"]
    )
    await insert_test_document(
        worker_conn, title="안내", content="보안 점검 안내", tags=["안내"]
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="보안 점검", tags=["규정"])

    assert [hit.document_id for hit in hits] == [tagged_id]


async def test_content_type_filter(worker_conn, search_conn):
    provider = FakeProvider()
    pdf_id = await insert_test_document(
        worker_conn, title="PDF", content="감사 보고서", content_type="pdf"
    )
    await insert_test_document(
        worker_conn, title="Markdown", content="감사 보고서", content_type="md"
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="감사 보고서", content_type="pdf")

    assert [hit.document_id for hit in hits] == [pdf_id]


async def test_structured_filter_and_vector_ranking_apply_together(worker_conn, search_conn):
    """SQL 거리순 정렬과 태그 필터의 결합을 본다. 의미 품질은 FakeProvider의 검증 대상이 아니다."""
    provider = FakeProvider()
    both_id = await insert_test_document(
        worker_conn, title="정합성 규정", content="OpenSQL 정합성 운영", tags=["규정"]
    )
    unrelated_id = await insert_test_document(
        worker_conn, title="복지 규정", content="식대 휴가 복지", tags=["규정"]
    )
    await insert_test_document(
        worker_conn, title="태그 불일치", content="OpenSQL 정합성 운영", tags=["안내"]
    )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", tags=["규정"])

    assert hits[0].document_id == both_id
    assert hits[0].score > next(hit.score for hit in hits if hit.document_id == unrelated_id)


async def test_long_document_appears_only_once(worker_conn, search_conn):
    provider = FakeProvider()
    long_id = await insert_test_document(
        worker_conn,
        title="긴 문서",
        content=("OpenSQL 정합성 " * 900),
    )
    await insert_test_document(worker_conn, title="짧은 문서", content="OpenSQL 정합성 안내")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", k=10)

    assert [hit.document_id for hit in hits].count(long_id) == 1


async def test_distinct_documents_are_finally_sorted_by_distance(worker_conn, search_conn):
    """SQL의 최종 거리순 정렬을 검증한다. 검색 품질은 BGE-M3의 성질이라 FakeProvider로 검증하지 않는다."""
    provider = FakeProvider()
    expected = await insert_test_document(
        worker_conn,
        document_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        title="가장 관련",
        content=("OpenSQL 정합성 " * 500),
    )
    for index in range(5):
        await insert_test_document(
            worker_conn,
            document_id=UUID(int=index + 1),
            title=f"무관 {index}",
            content=(f"휴가 복지 식대 {index} " * 200),
        )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성", k=3)

    assert hits[0].document_id == expected
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


async def test_pending_reembedding_keeps_previous_chunks_searchable(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn, title="수정 문서", content="OpenSQL 정합성 이전 내용"
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %s, content_hash = %s
         WHERE id = %s
        """,
        ("완전히 새로운 내용", "new-hash", document_id),
    )

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert document_id in {hit.document_id for hit in hits}


async def test_document_without_chunks_is_not_searchable(worker_conn, search_conn):
    provider = FakeProvider()
    document_id = await insert_test_document(
        worker_conn, title="미색인", content="OpenSQL 정합성 대기"
    )

    hits = await search_documents(search_conn, provider, query="OpenSQL 정합성")

    assert document_id not in {hit.document_id for hit in hits}


@pytest.mark.parametrize("k", [0, MAX_K + 1])
async def test_k_outside_supported_range_is_rejected(search_conn, k):
    with pytest.raises(ValueError, match="k는"):
        await search_documents(search_conn, FakeProvider(), query="질의", k=k)


async def test_max_k_is_accepted(worker_conn, search_conn):
    provider = FakeProvider()
    await insert_test_document(worker_conn, title="문서", content="최대 검색 건수")
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="최대 검색 건수", k=MAX_K)

    assert len(hits) == 1


async def test_ef_search_returns_max_k_distinct_documents(worker_conn, search_conn):
    provider = FakeProvider()
    for index in range(MAX_K + 5):
        await insert_test_document(
            worker_conn, title=f"문서 {index}", content=f"공통 검색어 고유{index}"
        )
    await process_all_embedding_jobs(worker_conn, provider)

    hits = await search_documents(search_conn, provider, query="공통 검색어", k=MAX_K)

    assert len(hits) == MAX_K


async def test_search_tuning_is_local_to_one_transaction(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await conn.execute("SELECT %s::vector", ("[" + ",".join(["0"] * 1024) + "]",))
        before_ef = (await (await conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
        before_rpc = (await (await conn.execute("SHOW random_page_cost")).fetchone())[0]

        async with conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
            await conn.execute("SET LOCAL random_page_cost = 1.1")
            inside_ef = (await (await conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
            inside_rpc = (await (await conn.execute("SHOW random_page_cost")).fetchone())[0]

        after_ef = (await (await conn.execute("SHOW hnsw.ef_search")).fetchone())[0]
        after_rpc = (await (await conn.execute("SHOW random_page_cost")).fetchone())[0]

    assert MAX_K * CANDIDATE_MULTIPLIER == EF_SEARCH
    assert inside_ef == str(EF_SEARCH)
    assert float(inside_rpc) == 1.1
    assert after_ef == before_ef
    assert after_rpc == before_rpc


async def test_explain_contains_structured_filters_and_vector_ordering(worker_conn, migrated_db):
    provider = FakeProvider()
    for index in range(10):
        await insert_test_document(
            worker_conn,
            title=f"계획 문서 {index}",
            content=f"OpenSQL 정합성 계획 {index}",
            tags=["규정"],
        )
    await process_all_embedding_jobs(worker_conn, provider)
    vector = provider.embed(["OpenSQL 정합성"])[0]
    vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
    params = {
        "qvec": vector_literal,
        "tags": ["규정"],
        "ctype": None,
        "user": "alice",
        "k": 5,
    }

    async with (
        await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn,
        conn.transaction(),
    ):
        await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        await conn.execute("SET LOCAL random_page_cost = 1.1")
        await conn.execute("SET LOCAL enable_seqscan = off")
        cur = await conn.execute("EXPLAIN " + SEARCH_SQL, params)
        plan = "\n".join(row[0] for row in await cur.fetchall())

    # 선택적인 태그 필터에서는 작은 로컬 데이터에 HNSW보다 필터 우선 계획이 더 싸다.
    # 여기서는 인덱스 선택이 아니라 정형 필터와 벡터 정렬이 한 계획에 결합됨을 확인한다.
    assert "visibility" in plan and "owner_id" in plan, plan
    assert "tags" in plan and "<=>" in plan, plan
