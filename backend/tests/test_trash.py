"""휴지통 서비스의 보존·소유자 경계·감사를 실제 DB로 검증한다."""

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.embeddings import FakeProvider
from openarchive.services import audit, documents, trash
from openarchive.services.search import search_documents


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        await connection.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES "
            "('alice', 'hash', false), ('bob', 'hash', false), ('admin', 'hash', true)"
        )
        yield connection


@pytest.fixture
async def document(conn):
    folder = (await (await conn.execute(
        "INSERT INTO folders (name, created_by, visibility) "
        "VALUES ('보관', 'alice', 'public') RETURNING id"
    )).fetchone())[0]
    doc = await insert_test_document(conn, title="복원 대상", content="휴지통 검색 근거", tags=["보관"])
    await conn.execute(
        "UPDATE documents SET folder_id = %s, follows_folder = true WHERE id = %s", (folder, doc)
    )
    await conn.execute(
        "INSERT INTO document_grants (document_id, user_id) VALUES (%s, (SELECT id FROM users WHERE username = 'bob'))", (doc,)
    )
    await conn.execute(
        "INSERT INTO document_files "
        "(document_id, file_version, filename, data, text_version, uploaded_by) "
        "VALUES (%s, 1, 'original.txt', %s, 1, 'alice')", (doc, b"original")
    )
    await insert_test_document(conn, title="이웃", content="휴지통 검색 근거")
    await process_all_embedding_jobs(conn, FakeProvider())
    return doc


async def snapshot(conn, doc):
    result = {}
    for table, condition in (
        ("documents", "id = %s"),
        ("document_chunks", "document_id = %s"),
        ("embedding_jobs", "document_id = %s"),
        ("document_versions", "document_id = %s"),
        ("document_files", "document_id = %s"),
        ("document_grants", "document_id = %s"),
        ("document_edges", "src_document_id = %s OR dst_document_id = %s"),
    ):
        # JSON snapshots retain all stored fields, including vectors and original bytes.
        rows = await (await conn.execute(
            f"SELECT to_jsonb(t) FROM {table} t WHERE {condition} ORDER BY to_jsonb(t)::text",
            (doc, doc) if table == "document_edges" else (doc,),
        )).fetchall()
        result[table] = [row[0] for row in rows]
    for row in result["documents"]:
        row.pop("deleted_at")
    return result


async def test_trash_and_restore_preserve_data_and_search(conn, document):
    before = await snapshot(conn, document)
    assert before["document_chunks"] and before["document_edges"]
    detail = await documents.get_document(conn, document, user_id="alice")
    async with conn.transaction():
        await audit.set_actor(conn, actor="alice", via="session")
        await trash.trash_document(conn, document, user_id="alice")
    assert (await (await conn.execute(
        "SELECT deleted_at FROM documents WHERE id = %s", (document,)
    )).fetchone())[0] is not None
    assert document not in {d["id"] for d in await documents.list_documents(conn, user_id="alice")}
    assert await snapshot(conn, document) == before
    with pytest.raises(documents.DocumentNotFound):
        await trash.trash_document(conn, document, user_id="alice")
    async with conn.transaction():
        await audit.set_actor(conn, actor="alice", via="session")
        restored = await trash.restore_document(conn, document, user_id="alice")
    assert restored == detail
    assert await snapshot(conn, document) == before
    assert document in {d["id"] for d in await documents.list_documents(conn, user_id="alice")}
    assert document in {h.document_id for h in await search_documents(
        conn, FakeProvider(), query="휴지통 검색 근거", user_id="alice"
    )}
    rows = await (await conn.execute(
        "SELECT action, actor, actor_via FROM audit_log WHERE document_id = %s "
        "AND action IN ('document_trashed', 'document_restored') ORDER BY id", (document,)
    )).fetchall()
    assert rows == [("document_trashed", "alice", "session"),
                    ("document_restored", "alice", "session")]


@pytest.mark.parametrize("user", ["bob", "admin"])
async def test_trash_nonowner_uses_existing_write_denial(conn, document, user):
    with pytest.raises(documents.DocumentAccessDenied):
        await trash.trash_document(conn, document, user_id=user)


async def test_list_trash_owner_order_and_deadline(conn):
    ids = [await insert_test_document(conn, title=title, content=title, owner_id=owner)
           for title, owner in [("옛것", "alice"), ("새것", "alice"), ("남의 것", "bob")]]
    for doc, days in zip(ids, [2, 1, 1], strict=True):
        await conn.execute("UPDATE documents SET deleted_at = now() - %s * interval '1 day' "
                           "WHERE id = %s", (days, doc))
    await insert_test_document(conn, title="활성", content="활성")
    rows = await trash.list_trash(conn, user_id="alice", retention_days=17)
    assert [r["id"] for r in rows] == [ids[1], ids[0]]
    assert [r["title"] for r in rows] == ["새것", "옛것"]
    assert all(set(r) == {"id", "title", "deleted_at", "purge_at"} for r in rows)
    assert all(r["purge_at"] == r["deleted_at"] + timedelta(days=17) for r in rows)
    assert await trash.list_trash(conn, user_id="admin", retention_days=30) == []
    assert [r["id"] for r in await trash.list_trash(conn, user_id="bob", retention_days=30)] == [ids[2]]


@pytest.mark.parametrize("operation", ["restore_document", "purge_document"])
@pytest.mark.parametrize("user", ["bob", "admin"])
async def test_owner_only_operations_hide_others(conn, document, operation, user):
    await trash.trash_document(conn, document, user_id="alice")
    with pytest.raises(documents.DocumentNotFound):
        await getattr(trash, operation)(conn, document, user_id=user)
    assert len(await trash.list_trash(conn, user_id="alice", retention_days=30)) == 1


@pytest.mark.parametrize("operation", ["trash_document", "restore_document", "purge_document"])
async def test_missing_document(conn, operation):
    with pytest.raises(documents.DocumentNotFound):
        await getattr(trash, operation)(conn, uuid4(), user_id="alice")


async def test_restore_requires_trash(conn, document):
    with pytest.raises(documents.DocumentNotFound):
        await trash.restore_document(conn, document, user_id="alice")


@pytest.mark.parametrize("trashed", [False, True])
async def test_purge_cascades_and_keeps_audit(conn, document, trashed):
    if trashed:
        await trash.trash_document(conn, document, user_id="alice")
    async with conn.transaction():
        await audit.set_actor(conn, actor="alice", via="session")
        await trash.purge_document(conn, document, user_id="alice")
    assert all(not rows for rows in (await snapshot(conn, document)).values())
    assert (await (await conn.execute(
        "SELECT action, actor, actor_via, document_title FROM audit_log "
        "WHERE document_id = %s ORDER BY id DESC LIMIT 1", (document,)
    )).fetchone()) == ("document_deleted", "alice", "session", "복원 대상")


async def test_purge_expired_strict_boundary_and_worker_actor(conn):
    ids = [await insert_test_document(conn, title=str(i), content=str(i)) for i in range(4)]
    async with conn.transaction():
        await audit.set_actor(conn, actor="alice", via="session")
        for doc, age in zip(ids[:3], [timedelta(days=17, seconds=1), timedelta(days=17),
                                     timedelta(days=17, seconds=-1)], strict=True):
            await conn.execute("UPDATE documents SET deleted_at = now() - %s WHERE id = %s", (age, doc))
        assert await trash.purge_expired(conn, retention_days=17) == 1
        assert {r[0] for r in await (await conn.execute("SELECT id FROM documents")).fetchall()} == set(ids[1:])
        assert (await (await conn.execute(
            "SELECT actor, actor_via FROM audit_log WHERE document_id = %s "
            "AND action = 'document_deleted'", (ids[0],)
        )).fetchone()) == (None, "worker")
    # Also exercise the function's transaction on an idle autocommit connection.
    await conn.execute("UPDATE documents SET deleted_at = now() - interval '18 days' WHERE id = %s", (ids[1],))
    assert await trash.purge_expired(conn, retention_days=17) == 1
    assert await trash.purge_expired(conn, retention_days=17) == 0
    assert (await (await conn.execute("SELECT current_setting('openarchive.actor_via', true)")).fetchone())[0] == ""
