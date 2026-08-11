import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.auth import hash_password
from app.services.related import find_related, suggest_tags
from app.services.search import search_documents


@pytest.fixture
async def worker_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def visibility_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
        yield conn


@pytest.fixture
async def visible_documents(worker_conn):
    provider = FakeProvider()
    content = "OpenSQL 권한 경계와 문서 관계"
    public_id = await insert_test_document(
        worker_conn,
        title="공개 문서",
        content=content,
        owner_id="alice",
        visibility="public",
    )
    alice_private_id = await insert_test_document(
        worker_conn,
        title="앨리스 비공개 문서",
        content=content,
        owner_id="alice",
        visibility="private",
        tags=["alice-secret"],
    )
    bob_private_id = await insert_test_document(
        worker_conn,
        title="밥 비공개 문서",
        content="OpenSQL 권한 경계와 비공개 운영",
        owner_id="bob",
        visibility="private",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    return provider, public_id, alice_private_id, bob_private_id


async def test_anonymous_search_hides_private_documents(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, _ = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id=None
    )

    assert [hit.document_id for hit in hits] == [public_id]
    assert alice_private_id not in {hit.document_id for hit in hits}


async def test_other_user_search_hides_alice_private_document(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, bob_private_id = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="bob"
    )

    assert {hit.document_id for hit in hits} == {public_id, bob_private_id}
    assert len(hits) == 2
    assert alice_private_id not in {hit.document_id for hit in hits}


async def test_owner_search_includes_own_private_document(
    visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, _ = visible_documents

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="alice"
    )

    assert {hit.document_id for hit in hits} == {public_id, alice_private_id}
    assert len(hits) == 2


async def test_admin_search_still_hides_other_users_private_documents(
    worker_conn, visibility_conn, visible_documents
):
    provider, public_id, alice_private_id, bob_private_id = visible_documents
    await worker_conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', %s, true)",
        (hash_password("admin-secret"),),
    )

    hits = await search_documents(
        visibility_conn, provider, query="OpenSQL 권한 경계", user_id="admin"
    )

    assert [hit.document_id for hit in hits] == [public_id]
    assert alice_private_id not in {hit.document_id for hit in hits}
    assert bob_private_id not in {hit.document_id for hit in hits}


@pytest.mark.parametrize(
    ("user_id", "can_traverse_through_private"),
    [(None, False), ("bob", False), ("alice", True)],
)
async def test_graph_search_cannot_traverse_through_an_invisible_private_document(
    worker_conn, visibility_conn, user_id, can_traverse_through_private
):
    provider = FakeProvider()
    entry_id = await insert_test_document(
        worker_conn,
        title="공개 진입점",
        content=("그래프 경유 차단 질의 " * 2000),
    )
    private_id = await insert_test_document(
        worker_conn,
        title="비공개 중간 노드",
        content="중간 비공개 문서",
        owner_id="alice",
        visibility="private",
    )
    beyond_id = await insert_test_document(
        worker_conn,
        title="비공개 너머 공개 노드",
        content="너머 공개 문서",
    )
    await process_all_embedding_jobs(worker_conn, provider)
    await worker_conn.execute("DELETE FROM document_edges")
    await worker_conn.execute(
        """
        INSERT INTO document_edges
            (src_document_id, dst_document_id, kind,
             src_chunk_index, dst_chunk_index, score)
        VALUES (%s, %s, 'related', 0, 0, 0.9),
               (%s, %s, 'related', 0, 0, 0.8)
        """,
        (entry_id, private_id, private_id, beyond_id),
    )

    hits = await search_documents(
        visibility_conn,
        provider,
        query="그래프 경유 차단 질의",
        user_id=user_id,
        k=3,
    )
    ids = {hit.document_id for hit in hits}

    assert (private_id in ids) is can_traverse_through_private
    assert (beyond_id in ids) is can_traverse_through_private


async def test_anonymous_related_hides_private_documents(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id=None
    )

    assert result.items == []
    assert alice_private_id not in {item.document_id for item in result.items}


async def test_other_user_related_hides_alice_private_document(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, bob_private_id = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id="bob"
    )

    assert [item.document_id for item in result.items] == [bob_private_id]
    assert len(result.items) == 1
    assert alice_private_id not in {item.document_id for item in result.items}


async def test_owner_related_includes_own_private_document(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    result = await find_related(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert [item.document_id for item in result.items] == [alice_private_id]
    assert len(result.items) == 1


async def test_tag_suggestions_do_not_leave_private_placeholders(
    visibility_conn, visible_documents
):
    _, public_id, _, _ = visible_documents

    anonymous = await suggest_tags(
        visibility_conn, document_id=public_id, user_id=None
    )
    other_user = await suggest_tags(
        visibility_conn, document_id=public_id, user_id="bob"
    )
    owner = await suggest_tags(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert anonymous.items == []
    assert other_user.items == []
    assert [(item.tag, item.freq) for item in owner.items] == [("alice-secret", 1)]
    assert len(owner.items) == 1


async def test_identical_documents_do_not_leave_private_placeholders(
    visibility_conn, visible_documents
):
    _, public_id, alice_private_id, _ = visible_documents

    anonymous = await find_related(
        visibility_conn, document_id=public_id, user_id=None
    )
    other_user = await find_related(
        visibility_conn, document_id=public_id, user_id="bob"
    )
    owner = await find_related(
        visibility_conn, document_id=public_id, user_id="alice"
    )

    assert anonymous.identical == []
    assert other_user.identical == []
    assert [item.document_id for item in owner.identical] == [alice_private_id]
    assert len(owner.identical) == 1
