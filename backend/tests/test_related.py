from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.related import (
    find_related,
    suggest_tags,
)
from app.services.search import CANDIDATE_MULTIPLIER as SEARCH_CANDIDATE_MULTIPLIER
from app.services.search import EF_SEARCH, MAX_K


@pytest.fixture
async def worker_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def related_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


async def test_related_documents_are_ranked_by_score_and_exclude_the_source(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="기준", content="OpenSQL 정합성 트리거 운영"
    )
    closest_id = await insert_test_document(
        worker_conn, title="관련", content="OpenSQL 정합성 트리거 안내"
    )
    await insert_test_document(worker_conn, title="무관", content="휴가 식대 복지 안내")
    await process_all_embedding_jobs(worker_conn, provider)

    result = await find_related(related_conn, document_id=source_id)

    assert closest_id in {item.document_id for item in result.items}
    assert result.items[0].kind in {"overlaps", "related"}
    assert source_id not in {item.document_id for item in result.items}
    for kind in {item.kind for item in result.items}:
        scores = [item.score for item in result.items if item.kind == kind]
        assert scores == sorted(scores, reverse=True)


async def test_tag_suggestions_are_sorted_by_frequency_then_name(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="기준", content="OpenSQL 정합성 트리거 운영"
    )
    await insert_test_document(
        worker_conn,
        title="관련 1",
        content="OpenSQL 정합성 트리거 안내",
        tags=["database", "opensql", "worker"],
    )
    await insert_test_document(
        worker_conn,
        title="관련 2",
        content="OpenSQL 정합성 운영 안내",
        tags=["database", "opensql"],
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await suggest_tags(related_conn, document_id=source_id)

    assert [(item.tag, item.freq) for item in result.items] == [
        ("database", 2),
        ("opensql", 2),
        ("worker", 1),
    ]
    assert result.based_on_version == 1
    assert result.reason is None


async def test_tag_suggestions_exclude_tags_already_on_the_source(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn,
        title="기준",
        content="OpenSQL 정합성 트리거 운영",
        tags=["opensql"],
    )
    await insert_test_document(
        worker_conn,
        title="관련",
        content="OpenSQL 정합성 트리거 안내",
        tags=["opensql", "database"],
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await suggest_tags(related_conn, document_id=source_id)

    assert [(item.tag, item.freq) for item in result.items] == [("database", 1)]


async def test_tag_suggestions_do_not_leak_tags_from_another_users_private_document(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn,
        title="기준",
        content="기밀 접근통제 운영",
        owner_id="alice",
    )
    await insert_test_document(
        worker_conn,
        title="공개",
        content="기밀 접근통제 안내",
        tags=["public-tag"],
    )
    await insert_test_document(
        worker_conn,
        title="타인 비공개",
        content="기밀 접근통제 지침",
        owner_id="bob",
        visibility="private",
        tags=["secret-tag"],
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await suggest_tags(
        related_conn, document_id=source_id, user_id="alice"
    )

    assert [item.tag for item in result.items] == ["public-tag"]


async def test_tag_suggestions_return_not_indexed_without_source_chunks(
    worker_conn, related_conn
):
    source_id = await insert_test_document(
        worker_conn, title="기준", content="아직 색인되지 않은 문서"
    )

    result = await suggest_tags(related_conn, document_id=source_id)

    assert result.items == []
    assert result.based_on_version is None
    assert result.reason == "not_indexed"


@pytest.mark.parametrize("with_untagged_neighbor", [False, True])
async def test_tag_suggestions_allow_an_empty_cold_start(
    worker_conn, related_conn, with_untagged_neighbor
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="기준", content="OpenSQL 정합성 트리거 운영"
    )
    if with_untagged_neighbor:
        await insert_test_document(
            worker_conn, title="태그 없는 이웃", content="OpenSQL 정합성 트리거 안내"
        )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await suggest_tags(related_conn, document_id=source_id)

    assert result.items == []
    assert result.based_on_version == 1
    assert result.reason is None


async def test_tag_suggestion_limit_restricts_the_number_of_items(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="기준", content="OpenSQL 정합성 트리거 운영"
    )
    await insert_test_document(
        worker_conn,
        title="관련",
        content="OpenSQL 정합성 트리거 안내",
        tags=["alpha", "beta", "gamma"],
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await suggest_tags(related_conn, document_id=source_id, limit=2)

    assert [(item.tag, item.freq) for item in result.items] == [
        ("alpha", 1),
        ("beta", 1),
    ]


async def test_related_documents_apply_candidate_visibility(worker_conn, related_conn):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="기준", content="기밀 접근통제 운영", owner_id="alice"
    )
    own_private_id = await insert_test_document(
        worker_conn,
        title="내 비공개",
        content="기밀 접근통제 안내",
        owner_id="alice",
        visibility="private",
    )
    other_private_id = await insert_test_document(
        worker_conn,
        title="타인 비공개",
        content="기밀 접근통제 지침",
        owner_id="bob",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await find_related(
        related_conn, document_id=source_id, user_id="alice"
    )

    result_ids = {item.document_id for item in result.items}
    assert own_private_id in result_ids
    assert other_private_id not in result_ids


async def test_document_without_chunks_returns_only_identical_documents(
    worker_conn, related_conn
):
    content = "아직 색인되지 않은 동일 텍스트"
    source_id = await insert_test_document(worker_conn, title="기준", content=content)
    identical_id = await insert_test_document(worker_conn, title="동일", content=content)
    await insert_test_document(worker_conn, title="다른 문서", content="완전히 다른 텍스트")

    result = await find_related(related_conn, document_id=source_id)

    assert result.items == []
    assert result.reason == "not_indexed"
    assert result.based_on_version is None
    assert [item.document_id for item in result.identical] == [identical_id]


async def test_indexed_document_without_edges_returns_no_edges(worker_conn, related_conn):
    source_id = await insert_test_document(
        worker_conn, title="고립", content="관련 문서가 없는 색인 문서"
    )
    await process_all_embedding_jobs(worker_conn, FakeProvider())

    result = await find_related(related_conn, document_id=source_id)

    assert result.items == []
    assert result.based_on_version == 1
    assert result.reason == "no_edges"


async def test_pending_reembedding_uses_the_existing_chunk_version(
    worker_conn, related_conn
):
    provider = FakeProvider()
    source_id = await insert_test_document(
        worker_conn, title="수정 중", content="OpenSQL 정합성 이전 내용"
    )
    await insert_test_document(
        worker_conn, title="관련", content="OpenSQL 정합성 관련 내용"
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %s, content_hash = %s
         WHERE id = %s
        """,
        ("새로운 추출 텍스트", "new-content-hash", source_id),
    )

    result = await find_related(related_conn, document_id=source_id)

    assert result.items
    assert result.based_on_version == 1
    assert result.reason is None


async def test_identical_documents_apply_visibility_even_when_indexed(
    worker_conn, related_conn
):
    provider = FakeProvider()
    content = "동일 텍스트 권한 검증"
    source_id = await insert_test_document(
        worker_conn, title="기준", content=content, owner_id="alice"
    )
    public_id = await insert_test_document(worker_conn, title="공개", content=content)
    own_private_id = await insert_test_document(
        worker_conn,
        title="내 비공개",
        content=content,
        owner_id="alice",
        visibility="private",
    )
    await insert_test_document(
        worker_conn,
        title="타인 비공개",
        content=content,
        owner_id="bob",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)

    result = await find_related(
        related_conn, document_id=source_id, user_id="alice"
    )

    assert {item.document_id for item in result.identical} == {
        public_id,
        own_private_id,
    }


@pytest.mark.parametrize("k", [0, MAX_K + 1])
async def test_k_outside_supported_range_is_rejected(related_conn, k):
    with pytest.raises(ValueError, match="k는"):
        await find_related(related_conn, document_id=UUID(int=0), k=k)


def test_candidate_limits_stay_under_ef_search():
    """후보 LIMIT이 ef_search에 **닿기만 해도** HNSW가 에러 없이 행을 덜 돌려준다.

    실측으로 ef_search=200에서 LIMIT 200은 193행만 반환했다. 등호도 안전하지 않으므로
    k 상한에서도 여유가 남아야 한다 (ADR-011 보강 4).
    """
    assert MAX_K * SEARCH_CANDIDATE_MULTIPLIER < EF_SEARCH
