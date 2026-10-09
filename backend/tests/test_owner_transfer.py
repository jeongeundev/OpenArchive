"""소유권 이전의 열람·부여·감사·일괄 삭제 계약."""
from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services import auth
from openarchive.services import documents as d
from openarchive.services import folders as f
from openarchive.services.audit import set_actor
from openarchive.services.grants import create_group, insert_grants, resolve_grantees
from openarchive.services.shares import add_document, create_share


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
        for name in ["kim", "lee", "other", "admin"]:
            await c.execute(
                "INSERT INTO users(username,password_hash,is_admin) VALUES (%s,'unused',%s)",
                (name, name == "admin"),
            )
        yield c


async def doc(conn, **kwargs):
    return await insert_test_document(conn, title="문서", content="내용", owner_id="kim", **kwargs)


async def scalar(conn, sql, args=()):
    return (await (await conn.execute(sql, args)).fetchone())[0]


@pytest.mark.parametrize("visibility,visible", [("public", True), ("private", False)])
async def test_document_transfer_changes_writes_and_visibility(conn, visibility, visible):
    id = await doc(conn, visibility=visibility)
    async with conn.transaction():
        await conn.execute("SELECT set_config('openarchive.actor_id','kim',true)")
        assert await d.transfer_owner(conn, id, user_id="kim", new_owner="lee") == {
            "owner_id": "lee", "still_visible": visible,
        }
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "lee"
    assert await scalar(conn, "SELECT actor FROM audit_log WHERE action='owner_changed'") == "kim"
    await d.update_extracted_text(conn, id, user_id="lee", content="새 내용", client_version=1)
    await d.update_tags(conn, id, user_id="lee", tags=["태그"])
    await d.set_access(conn, id, user_id="lee", visibility=visibility, users=[], groups=[])
    if visible:
        await d.get_document(conn, id, user_id="kim")
    else:
        with pytest.raises(d.DocumentNotFound):
            await d.get_document(conn, id, user_id="kim")
    for call in [
        d.update_tags(conn, id, user_id="kim", tags=[]),
        d.update_extracted_text(conn, id, user_id="kim", content="덮기", client_version=2),
        d.set_access(conn, id, user_id="kim", visibility="public", users=[], groups=[]),
    ]:
        with pytest.raises(d.DocumentAccessDenied if visible else d.DocumentNotFound):
            await call


@pytest.mark.parametrize("user,target,visibility,trashed,error", [
    ("other", "lee", "public", False, d.DocumentAccessDenied),
    ("other", "lee", "private", False, d.DocumentNotFound),
    (None, "lee", "public", False, d.DocumentNotFound),
    ("kim", "missing", "public", False, d.InvalidNewOwner),
    ("kim", "kim", "public", False, d.InvalidNewOwner),
    ("kim", "lee", "public", True, d.DocumentNotFound),
])
async def test_document_transfer_rejection_is_atomic(conn, user, target, visibility, trashed, error):
    id = await doc(conn, visibility=visibility)
    if trashed:
        await conn.execute("UPDATE documents SET deleted_at=now() WHERE id=%s", (id,))
    with pytest.raises(error):
        await d.transfer_owner(conn, id, user_id=user, new_owner=target)
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "kim"
    assert await scalar(conn, "SELECT count(*) FROM audit_log WHERE action='owner_changed'") == 0


async def test_document_transfer_removes_share_and_new_owner_grants_only(conn):
    id = await doc(conn, visibility="private")
    group = await create_group(conn, "팀")
    users, groups = await resolve_grantees(conn, users=["lee", "other"], groups=["팀"])
    await insert_grants(conn, id, users, groups)
    share = await create_share(conn, owner="kim", name="공유")
    await add_document(conn, share["id"], id, owner="kim")
    before = await scalar(conn, "SELECT coalesce(max(id), 0) FROM audit_log")
    async with conn.transaction():
        await set_actor(conn, actor="kim", via="session")
        await d.transfer_owner(conn, id, user_id="kim", new_owner="lee")
    rows = await (await conn.execute(
        "SELECT actor, actor_via, document_id, document_title, detail FROM audit_log "
        "WHERE id > %s AND action='share_changed'", (before,),
    )).fetchall()
    assert rows == [("kim", "session", id, "문서", {
        "change": "document_removed", "share_id": str(share["id"]), "share_name": "공유",
        "owner": "kim",
    })]
    access = await d.get_access(conn, id, user_id="lee")
    assert access["users"] == ["other"] and access["groups"] == ["팀"]
    assert await scalar(conn, "SELECT count(*) FROM document_grants WHERE share_id=%s", (share["id"],)) == 0
    assert await scalar(conn, "SELECT count(*) FROM document_grants WHERE group_id=%s", (group["id"],)) == 1
    await d.set_access(conn, id, user_id="lee", visibility=access["visibility"],
                       users=access["users"], groups=access["groups"])


async def test_folder_transfer_is_one_row_and_removes_target_grant(conn):
    root = await f.create_folder(conn, user_id="kim", name="루트", visibility="private", grant_users=["lee"])
    child = await f.create_folder(conn, user_id="kim", name="하위", parent_id=root["id"])
    id = await doc(conn)
    await conn.execute("UPDATE documents SET folder_id=%s WHERE id=%s", (root["id"], id))
    assert await f.transfer_folder_owner(conn, root["id"], user_id="kim", new_owner="lee") == {
        "created_by": "lee", "still_visible": False,
    }
    assert await scalar(conn, "SELECT count(*) FROM folder_grants WHERE folder_id=%s", (root["id"],)) == 0
    assert await scalar(conn, "SELECT count(*) FROM audit_log WHERE action='owner_changed' AND detail->>'kind'='folder'") == 1
    assert await scalar(conn, "SELECT created_by FROM folders WHERE id=%s", (child["id"],)) == "kim"
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "kim"
    await f.set_folder_access(conn, root["id"], user_id="lee", visibility="public", users=[], groups=[])
    with pytest.raises(f.NotFolderCreator):
        await f.set_folder_access(conn, root["id"], user_id="kim", visibility="public", users=[], groups=[])
    assert (await f.transfer_folder_owner(conn, child["id"], user_id="kim", new_owner="lee"))["still_visible"]


@pytest.mark.parametrize("user,target,visibility,error", [
    ("lee", "other", "public", f.NotFolderCreator),
    ("admin", "lee", "public", f.NotFolderCreator),
    ("lee", "other", "private", f.FolderNotFound),
    ("kim", "missing", "public", d.InvalidNewOwner),
    ("kim", "kim", "public", d.InvalidNewOwner),
])
async def test_folder_transfer_rejection(conn, user, target, visibility, error):
    root = await f.create_folder(conn, user_id="kim", name="폴더", visibility=visibility)
    with pytest.raises(error):
        await f.transfer_folder_owner(conn, root["id"], user_id=user, new_owner=target)
    assert await scalar(conn, "SELECT created_by FROM folders WHERE id=%s", (root["id"],)) == "kim"


async def test_delete_user_transfers_all_including_trash_and_audits(conn):
    ids = [await doc(conn, visibility="private") for _ in range(2)]
    await conn.execute("UPDATE documents SET deleted_at=now() WHERE id=%s", (ids[1],))
    root = await f.create_folder(conn, user_id="kim", name="루트", visibility="private", grant_users=["lee"])
    await f.create_folder(conn, user_id="kim", name="하위", parent_id=root["id"])
    users, groups = await resolve_grantees(conn, users=["lee"], groups=[])
    for id in ids:
        await insert_grants(conn, id, users, groups)
    share = await create_share(conn, owner="kim", name="공유")
    await add_document(conn, share["id"], ids[0], owner="kim")
    uid = await scalar(conn, "SELECT id FROM users WHERE username='kim'")
    with pytest.raises(auth.UserOwnsDocuments):
        await auth.delete_user(conn, uid)
    async with conn.transaction():
        await conn.execute("SELECT set_config('openarchive.actor_id','admin',true)")
        await auth.delete_user(conn, uid, transfer_to="lee")
    assert await scalar(conn, "SELECT count(*) FROM users WHERE id=%s", (uid,)) == 0
    assert await scalar(conn, "SELECT count(*) FROM documents WHERE owner_id='lee'") == 2
    assert await scalar(conn, "SELECT count(*) FROM folders WHERE created_by='lee'") == 2
    assert await scalar(conn, "SELECT count(*) FROM document_grants") == 0
    assert await scalar(conn, "SELECT count(*) FROM folder_grants") == 0
    assert await scalar(conn, "SELECT count(*) FROM shares") == 0
    assert await scalar(conn, "SELECT count(*) FROM audit_log WHERE action='owner_changed' AND actor='admin'") == 4
    with pytest.raises(d.DocumentNotFound):
        await d.get_document(conn, ids[0], user_id="admin")


@pytest.mark.parametrize("target", ["missing", "kim"])
async def test_delete_invalid_target_changes_nothing(conn, target):
    id = await doc(conn)
    uid = await scalar(conn, "SELECT id FROM users WHERE username='kim'")
    with pytest.raises(d.InvalidNewOwner):
        await auth.delete_user(conn, uid, transfer_to=target)
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "kim"
    assert await scalar(conn, "SELECT count(*) FROM users WHERE id=%s", (uid,)) == 1


async def test_delete_rejects_transfer_to_acting_admin(conn):
    # 관리자는 이전만 하고 열람을 얻지 않는다(ADR-061 결정 2) — 자기에게 옮기면 소유자로서 읽게 된다.
    id = await doc(conn, visibility="private")
    uid = await scalar(conn, "SELECT id FROM users WHERE username='kim'")
    with pytest.raises(d.InvalidNewOwner):
        await auth.delete_user(conn, uid, transfer_to="admin", actor="admin")
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "kim"
    assert await scalar(conn, "SELECT count(*) FROM users WHERE id=%s", (uid,)) == 1
    await auth.delete_user(conn, uid, transfer_to="lee", actor="admin")
    assert await scalar(conn, "SELECT owner_id FROM documents WHERE id=%s", (id,)) == "lee"


async def test_delete_empty_user_with_transfer_and_missing_source(conn):
    uid = await scalar(conn, "SELECT id FROM users WHERE username='kim'")
    await auth.delete_user(conn, uid, transfer_to="lee")
    assert await scalar(conn, "SELECT count(*) FROM users WHERE id=%s", (uid,)) == 0
    with pytest.raises(auth.UserNotFound):
        await auth.delete_user(conn, uuid4(), transfer_to="lee")
