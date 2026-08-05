from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.related import find_related
from app.services.search import MAX_K


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

    assert result.items[0].document_id == closest_id
    assert source_id not in {item.document_id for item in result.items}
    assert [item.score for item in result.items] == sorted(
        (item.score for item in result.items), reverse=True
    )


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
