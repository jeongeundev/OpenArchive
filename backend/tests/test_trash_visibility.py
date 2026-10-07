"""휴지통은 소유자·공유 주체에게도 존재하지 않는다 (ADR-060)."""

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.config import get_settings
from openarchive.db import close_pool, get_pool
from openarchive.embeddings import FakeProvider
from openarchive.services.answer import gather_evidence
from openarchive.services.auth import UserOwnsDocuments, delete_user
from openarchive.services.clusters import get_clusters
from openarchive.services.diagnostics import get_diagnostics
from openarchive.services.documents import (
    DocumentNotFound,
    count_documents,
    document_progress,
    find_same_original,
    find_same_text,
    get_document,
    list_documents,
    list_visible_tags,
    update_tags,
)
from openarchive.services.folders import FolderNotEmpty, delete_folder, list_folders
from openarchive.services.links import find_backlinks, resolve_links
from openarchive.services.related import find_related, suggest_tags
from openarchive.services.search import search_documents
from openarchive.services.shares import create_share, list_shares
from openarchive.services.visibility import share_principal

QUERY = "휴지통 검색 근거"


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


@pytest.fixture
async def archived(conn):
    await conn.execute("INSERT INTO users (username, password_hash) VALUES ('alice', 'hash')")
    cur = await conn.execute(
        "INSERT INTO folders (name, created_by, visibility) "
        "VALUES ('보관', 'alice', 'public') RETURNING id"
    )
    folder = (await cur.fetchone())[0]
    source = await insert_test_document(conn, title="출처", content=f"{QUERY} [[삭제 대상]]")
    target = await insert_test_document(
        conn, title="삭제 대상", content=f"{QUERY} [[출처]]", tags=["휴지통전용"]
    )
    twin = await insert_test_document(conn, title="쌍둥이", content=f"{QUERY} [[출처]]")
    await conn.execute("UPDATE documents SET folder_id = %s WHERE id = %s", (folder, target))
    await conn.execute(
        "INSERT INTO document_files "
        "(document_id, file_version, filename, data, text_version, uploaded_by) "
        "VALUES (%s, 1, 'target.txt', %s, 1, 'alice')", (target, b"original")
    )
    provider = FakeProvider()
    await process_all_embedding_jobs(conn, provider)
    await conn.execute("DELETE FROM document_edges")
    await conn.execute(
        "INSERT INTO document_edges (src_document_id, dst_document_id, kind, score) "
        "VALUES (%s, %s, 'overlaps', 1)", (target, source)
    )
    share = await create_share(conn, owner="alice", name="협업")
    await conn.execute(
        "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
        (target, share["id"]),
    )
    # 실제로 검색·관계·중복·링크·공유에 나타나는 문서를 지운다.
    assert target in {hit.document_id for hit in await search_documents(
        conn, provider, query=QUERY, user_id="alice"
    )}
    assert (await find_related(conn, document_id=source, user_id="alice")).items
    assert (await get_diagnostics(conn, user_id="alice")).duplicates.identical.count == 1
    assert (await resolve_links(conn, document_id=source, user_id="alice"))[0].document_id == target
    await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (target,))
    return provider, source, target, twin, folder, share["id"]


async def test_owner_cannot_read_trash_in_any_retrieval_path(conn, archived):
    provider, source, target, twin, folder, _ = archived
    assert {d["id"] for d in await list_documents(conn, user_id="alice")} == {source, twin}
    assert await count_documents(conn, user_id="alice") == 2
    assert sum((await document_progress(conn, user_id="alice")).values()) == 2
    assert "휴지통전용" not in await list_visible_tags(conn, user_id="alice")
    with pytest.raises(DocumentNotFound):
        await get_document(conn, target, user_id="alice")
    assert target not in {hit.document_id for hit in await search_documents(
        conn, provider, query=QUERY, user_id="alice"
    )}
    related = await find_related(conn, document_id=source, user_id="alice")
    assert related.items == []
    assert (await find_related(conn, document_id=twin, user_id="alice")).identical == []
    assert (await suggest_tags(conn, document_id=source, user_id="alice")).items == []
    clusters = await get_clusters(conn, user_id="alice")
    assert sum(c.size for c in clusters.clusters) == 2
    assert {d.document_id for c in clusters.clusters for d in c.documents} == {source, twin}
    diagnostics = await get_diagnostics(conn, user_id="alice")
    assert {d.document_id for d in diagnostics.orphans.items} == {source, twin}
    assert diagnostics.duplicates.identical.count == 0
    assert diagnostics.duplicates.overlaps.count == 0
    assert [(link.source.document_id, link.target_title)
            for link in diagnostics.broken_links.items] == [(source, "삭제 대상")]
    assert (await resolve_links(conn, document_id=source, user_id="alice"))[0].document_id is None
    assert {link.document_id for link in await find_backlinks(
        conn, document_id=source, user_id="alice"
    )} == {twin}
    evidence = await gather_evidence(
        conn, provider, query=QUERY, user_id="alice", context_chars=10000
    )
    assert evidence.sources
    assert target not in {item.document_id for item in evidence.sources}
    assert next(f for f in await list_folders(conn, user_id="alice")
                if f["id"] == folder)["document_count"] == 0


async def test_share_cannot_read_granted_trash(conn, archived):
    provider, _, target, _, _, share = archived
    principal = share_principal(share)
    assert await list_documents(conn, user_id=principal) == []
    assert await search_documents(conn, provider, query=QUERY, user_id=principal) == []
    with pytest.raises(DocumentNotFound):
        await get_document(conn, target, user_id=principal)


async def test_trash_cannot_be_written(conn, archived):
    _, _, target, _, _, _ = archived
    with pytest.raises(DocumentNotFound):
        await update_tags(conn, target, user_id="alice", tags=["변경"])


async def test_trash_still_prevents_folder_and_owner_deletion(conn, archived):
    _, _, _, _, folder, _ = archived
    with pytest.raises(FolderNotEmpty):
        await delete_folder(conn, folder, user_id="alice", is_admin=False)
    cur = await conn.execute(
        "INSERT INTO users (username, password_hash) VALUES ('trash-owner', 'hash') RETURNING id"
    )
    user = (await cur.fetchone())[0]
    doc = await insert_test_document(conn, title="소유 문서", content=QUERY, owner_id="trash-owner")
    await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
    with pytest.raises(UserOwnsDocuments):
        await delete_user(conn, user)


async def test_share_management_hides_trash_and_restoration_reveals_it(conn, archived):
    _, _, target, _, _, _ = archived
    assert (await list_shares(conn, owner="alice"))[0]["documents"] == []
    await conn.execute("UPDATE documents SET deleted_at = NULL WHERE id = %s", (target,))
    assert (await list_shares(conn, owner="alice"))[0]["documents"] == [
        {"id": target, "title": "삭제 대상"}
    ]


async def test_import_deduplication_ignores_trash(conn):
    target = await insert_test_document(conn, title="가져오기", content=QUERY)
    await conn.execute(
        "INSERT INTO document_files "
        "(document_id, file_version, filename, data, text_version, uploaded_by) "
        "VALUES (%s, 1, 'import.txt', %s, 1, 'alice')", (target, b"original")
    )
    assert await find_same_original(conn, owner_id="alice", data=b"original") == target
    assert await find_same_text(conn, owner_id="alice", content=QUERY) == target
    await conn.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (target,))
    assert await find_same_original(conn, owner_id="alice", data=b"original") is None
    assert await find_same_text(conn, owner_id="alice", content=QUERY) is None


async def test_mcp_search_hides_trash(monkeypatch, migrated_db, archived):
    _, source, target, twin, _, _ = archived
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("MCP_USER_ID", "alice")
    get_settings.cache_clear()
    from openarchive.mcp_server import server

    monkeypatch.setattr(server, "provider", FakeProvider())
    await get_pool().open()
    try:
        result = await server.search_documents(QUERY)
        assert {item["document_id"] for item in result["items"]} == {str(source), str(twin)}
        assert str(target) not in {item["document_id"] for item in result["items"]}
    finally:
        await close_pool()
