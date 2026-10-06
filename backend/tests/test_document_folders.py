"""문서 폴더 지정·열람·이동 계약."""

import hashlib
import json

import psycopg
import pytest

from openarchive.services import documents as d
from openarchive.services import folders as f
from openarchive.services.auth import UserOwnsDocuments, delete_user
from openarchive.services.shares import add_document, create_share


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
        for name in ["kim", "lee", "owner"]:
            await c.execute(
                "INSERT INTO users (username,password_hash) VALUES (%s,'unused')", (name,)
            )
        yield c


async def make(conn, folder_id=None, **kwargs):
    return await d.create_text_document(
        conn, title="문서", content="텍스트", owner_id="owner", folder_id=folder_id, **kwargs
    )


@pytest.mark.parametrize("file", [False, True])
async def test_creation_closes_own_scope(conn, file):
    folder = await f.create_folder(conn, user_id="kim", name="인사")
    if file:
        doc = await d.create_document(
            conn, filename="a.txt", data=b"text", owner_id="owner", folder_id=folder["id"]
        )
    else:
        doc = await make(conn, folder["id"])
    row = await (
        await conn.execute(
            "SELECT folder_id,follows_folder,visibility FROM documents WHERE id=%s", (doc["id"],)
        )
    ).fetchone()
    assert row == (folder["id"], True, "private")
    assert (
        await (
            await conn.execute(
                "SELECT count(*) FROM document_grants WHERE document_id=%s", (doc["id"],)
            )
        ).fetchone()
    )[0] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"visibility": "public"},
        {"visibility": "private"},
        {"grant_users": []},
        {"grant_groups": ["팀"]},
    ],
)
@pytest.mark.parametrize("file", [False, True])
async def test_explicit_scope_rejected(conn, kwargs, file):
    folder = await f.create_folder(conn, user_id="kim", name="인사")
    with pytest.raises(ValueError, match="폴더에 넣는 문서는 폴더의 열람 범위를 따릅니다"):
        if file:
            await d.create_document(
                conn,
                filename="a.txt",
                data=b"text",
                owner_id="owner",
                folder_id=folder["id"],
                **kwargs,
            )
        else:
            await make(conn, folder["id"], **kwargs)
    assert await d.count_documents(conn, user_id="owner") == 0


async def test_hidden_creation_and_move(conn):
    hidden = await f.create_folder(conn, user_id="kim", name="RFP", visibility="private")
    with pytest.raises(f.FolderNotFound):
        await make(conn, hidden["id"])
    assert await d.count_documents(conn, user_id="owner") == 0
    doc = await make(conn)
    with pytest.raises(f.FolderNotFound):
        await d.move_document(conn, doc["id"], user_id="owner", folder_id=hidden["id"])
    with pytest.raises(d.DocumentAccessDenied):
        await d.move_document(conn, doc["id"], user_id="lee", folder_id=None)
    await d.set_access(conn, doc["id"], user_id="owner", visibility="private", users=[], groups=[])
    with pytest.raises(d.DocumentNotFound):
        await d.move_document(conn, doc["id"], user_id="lee", folder_id=None)


@pytest.mark.parametrize("file", [False, True])
async def test_idempotency_folder_and_legacy(conn, file):
    a = await f.create_folder(conn, user_id="owner", name="A")
    b = await f.create_folder(conn, user_id="owner", name="B")

    async def create(folder):
        if file:
            return await d.create_document(
                conn,
                filename="a.txt",
                data=b"text",
                owner_id="owner",
                folder_id=folder,
                idempotency_key="key",
            )
        return await make(conn, folder, idempotency_key="key")

    first = await create(a["id"])
    assert (await create(a["id"]))["id"] == first["id"]
    with pytest.raises(d.IdempotencyKeyReused):
        await create(b["id"])
    await make(conn, idempotency_key="legacy")
    payload = {
        "kind": "text",
        "title": "문서",
        "content": "텍스트",
        "content_type": "md",
        "tags": None,
        "visibility": "public",
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert (
        await (
            await conn.execute("SELECT request_hash FROM idempotency_keys WHERE key='legacy'")
        ).fetchone()
    )[0] == expected


async def test_move_and_scope_switch(conn):
    public = await f.create_folder(conn, user_id="kim", name="인사")
    private = await f.create_folder(
        conn, user_id="kim", name="RFP", visibility="private", grant_users=["owner"]
    )
    child = await f.create_folder(conn, user_id="owner", name="채용", parent_id=public["id"])
    doc = await make(conn, public["id"])
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=private["id"])
    assert await d.count_documents(conn, user_id="lee") == 0
    access = await d.set_access(
        conn,
        doc["id"],
        user_id="owner",
        follows_folder=False,
        visibility="private",
        users=[],
        groups=[],
    )
    assert not access["follows_folder"]
    with pytest.raises(d.DocumentNotFound):
        await d.get_document(conn, doc["id"], user_id="kim")
    access = await d.set_access(conn, doc["id"], user_id="owner", follows_folder=True)
    assert access["visibility"] == "private" and access["users"] == []
    assert access["folder_scope"] == {"visibility": "private", "users": ["owner"], "groups": []}
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=child["id"])
    detail = await d.get_document(conn, doc["id"], user_id="lee")
    assert detail["folder"] == {
        "id": child["id"],
        "name": "채용",
        "path": [{"id": public["id"], "name": "인사"}, {"id": child["id"], "name": "채용"}],
    }
    await f.rename_folder(conn, child["id"], user_id="owner", is_admin=False, name="채용2")
    assert (await d.get_document(conn, doc["id"], user_id="lee"))["folder"]["name"] == "채용2"
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=None)
    assert (await d.get_access(conn, doc["id"], user_id="owner"))["follows_folder"]
    with pytest.raises(ValueError):
        await d.set_access(
            conn,
            doc["id"],
            user_id="owner",
            follows_folder=False,
            visibility="private",
            users=[],
            groups=[],
        )


async def test_hidden_folder_detail_summary_filter_and_share(conn):
    root = await f.create_folder(conn, user_id="owner", name="秘密", visibility="private")
    child = await f.create_folder(conn, user_id="owner", name="child", parent_id=root["id"])
    doc = await make(conn, root["id"])
    await make(conn, child["id"])
    await d.set_access(
        conn,
        doc["id"],
        user_id="owner",
        follows_folder=False,
        visibility="public",
        users=[],
        groups=[],
    )
    assert (await d.get_document(conn, doc["id"], user_id="lee"))["folder"] is None
    summary = (await d.list_documents(conn, user_id="lee"))[0]
    assert "folder_id" not in summary and "folder" not in summary
    assert await d.list_documents(conn, user_id="lee", folder_id=root["id"]) == []
    assert await d.count_documents(conn, user_id="lee", folder_id=root["id"]) == 0
    assert await d.count_documents(conn, user_id="owner", folder_id=root["id"]) == 1
    assert len(await d.list_documents(conn, user_id="owner", folder_id=root["id"])) == 1
    share = await create_share(conn, owner="owner", name="공유")
    await add_document(conn, share["id"], doc["id"], owner="owner")
    principal = f"share:{share['id']}"
    assert await d.count_documents(conn, user_id=principal) == 1
    assert await d.count_documents(conn, user_id=principal, folder_id=root["id"]) == 0
    assert await d.list_documents(conn, user_id=principal, folder_id=root["id"]) == []
    assert (await d.get_document(conn, doc["id"], user_id=principal))["folder"] is None
    await f.set_folder_access(
        conn, root["id"], user_id="owner", visibility="private", users=["kim"], groups=[]
    )
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=None)
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=root["id"])
    with pytest.raises(d.DocumentAccessDenied):
        await d.get_access(conn, doc["id"], user_id="kim")


async def test_folder_creator_cannot_be_deleted(conn):
    await f.create_folder(conn, user_id="kim", name="인사")
    user = (await (await conn.execute("SELECT id FROM users WHERE username='kim'")).fetchone())[0]
    with pytest.raises(UserOwnsDocuments, match="소유한 문서나 만든 폴더"):
        await delete_user(conn, user)


async def test_owner_access_hides_folder_after_revocation(conn):
    folder = await f.create_folder(
        conn, user_id="kim", name="RFP", visibility="private", grant_users=["owner"]
    )
    doc = await make(conn, folder["id"])
    await f.set_folder_access(
        conn, folder["id"], user_id="kim", visibility="private", users=[], groups=[]
    )
    access = await d.get_access(conn, doc["id"], user_id="owner")
    assert access["folder"] is None and access["folder_scope"] is None
    assert access["follows_folder"]
    assert (await d.get_document(conn, doc["id"], user_id="owner"))["folder"] is None
    with pytest.raises(f.FolderNotFound):
        await d.create_document(
            conn, filename="a.txt", data=b"text", owner_id="owner", folder_id=folder["id"]
        )


async def test_individual_scope_and_grants_survive_moves_and_return(conn):
    a = await f.create_folder(conn, user_id="owner", name="A")
    b = await f.create_folder(conn, user_id="owner", name="B")
    doc = await make(conn, a["id"])
    await d.set_access(
        conn,
        doc["id"],
        user_id="owner",
        follows_folder=False,
        visibility="private",
        users=["lee"],
        groups=[],
    )
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=b["id"])
    access = await d.get_access(conn, doc["id"], user_id="owner")
    assert access["follows_folder"] is False and access["users"] == ["lee"]
    await d.set_access(conn, doc["id"], user_id="owner", follows_folder=True)
    access = await d.set_access(
        conn,
        doc["id"],
        user_id="owner",
        follows_folder=False,
        visibility="private",
        users=["lee"],
        groups=[],
    )
    assert access["visibility"] == "private" and access["users"] == ["lee"]
    await d.move_document(conn, doc["id"], user_id="owner", folder_id=None)
    access = await d.get_access(conn, doc["id"], user_id="owner")
    assert access["follows_folder"] is False and access["users"] == ["lee"]


@pytest.mark.parametrize(
    "extra",
    [{"visibility": "private"}, {"users": ["lee"]}, {"groups": ["인사팀"]}],
)
async def test_follow_folder_rejects_individual_scope_values(conn, extra):
    # 함께 보낸 범위를 조용히 버리면, 좁혔다고 믿은 문서가 폴더 범위(조직 공개)를 따른다.
    await conn.execute("INSERT INTO groups (name) VALUES ('인사팀')")
    root = await f.create_folder(conn, user_id="kim", name="인사")
    doc = await make(conn, root["id"])
    await d.set_access(
        conn, doc["id"], user_id="owner", follows_folder=False,
        visibility="private", users=["kim"], groups=[],
    )
    with pytest.raises(ValueError, match="폴더 범위를 따르면 공개범위·부여 대상을 함께 지정할 수 없습니다."):
        await d.set_access(conn, doc["id"], user_id="owner", follows_folder=True, **extra)
    access = await d.get_access(conn, doc["id"], user_id="owner")
    assert access["follows_folder"] is False
    assert access["visibility"] == "private" and access["users"] == ["kim"]
