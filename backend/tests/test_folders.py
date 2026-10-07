"""폴더 서비스의 열람·관리·범위·직접 문서 집계 계약."""

from uuid import uuid4

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services import folders as f
from openarchive.services.documents import GrantsOnPublicDocument
from openarchive.services.grants import UnknownGrantee, add_member, create_group, remove_member


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
        for user in ["kim", "lee", "admin"]:
            await c.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, 'unused', %s)",
                (user, user == "admin"),
            )
        yield c


async def root(conn, **kwargs):
    return await f.create_folder(conn, user_id="kim", name="인사", **kwargs)


async def test_tree_scope_path_and_flags(conn):
    r = await root(conn)
    child = await f.create_folder(conn, user_id="lee", name="채용", parent_id=r["id"])
    tree = {row["id"]: row for row in await f.list_folders(conn, user_id="lee")}
    assert tree[r["id"]]["scope"] == {"visibility": "public", "users": [], "groups": []}
    assert not tree[r["id"]]["inherited"]
    assert not tree[r["id"]]["can_manage"]
    assert not tree[r["id"]]["can_change_access"]
    assert tree[child["id"]]["inherited"]
    assert tree[child["id"]]["can_manage"]
    assert not tree[child["id"]]["can_change_access"]
    assert tree[child["id"]]["parent_id"] == r["id"]
    assert await f.folder_path(conn, child["id"]) == [
        {"id": r["id"], "name": "인사"},
        {"id": child["id"], "name": "채용"},
    ]
    owner = (await f.list_folders(conn, user_id="kim"))[0]
    assert owner["can_manage"] and owner["can_change_access"]
    assert all(x["can_manage"] for x in await f.list_folders(conn, user_id="admin", is_admin=True))
    await f.rename_folder(conn, r["id"], user_id="kim", is_admin=False, name="인재")
    assert (await f.folder_path(conn, child["id"]))[0]["name"] == "인재"


@pytest.mark.parametrize("operation", ["rename", "delete", "get", "set"])
async def test_other_user_and_admin_access_boundary(conn, operation):
    r = await root(conn)
    for user in ["lee", "admin"]:
        with pytest.raises(f.NotFolderCreator):
            if operation == "rename":
                await f.rename_folder(conn, r["id"], user_id=user, is_admin=False, name="변경")
            elif operation == "delete":
                await f.delete_folder(conn, r["id"], user_id=user, is_admin=False)
            elif operation == "get":
                await f.get_folder_access(conn, r["id"], user_id=user)
            else:
                await f.set_folder_access(
                    conn, r["id"], user_id=user, visibility="private", users=[], groups=[]
                )
    if operation == "rename":
        assert (await f.rename_folder(conn, r["id"], user_id="admin", is_admin=True, name="변경"))[
            "name"
        ] == "변경"
    if operation == "delete":
        await f.delete_folder(conn, r["id"], user_id="admin", is_admin=True)
        assert await f.list_folders(conn, user_id="kim") == []


@pytest.mark.parametrize("user", ["lee", "admin"])
@pytest.mark.parametrize("operation", ["rename", "delete", "get", "set", "child"])
async def test_hidden_folder_is_not_found_before_permission(conn, user, operation):
    r = await root(conn, visibility="private")
    with pytest.raises(f.FolderNotFound):
        if operation == "rename":
            await f.rename_folder(conn, r["id"], user_id=user, is_admin=True, name="변경")
        elif operation == "delete":
            await f.delete_folder(conn, r["id"], user_id=user, is_admin=True)
        elif operation == "get":
            await f.get_folder_access(conn, r["id"], user_id=user)
        elif operation == "set":
            await f.set_folder_access(
                conn, r["id"], user_id=user, visibility="public", users=[], groups=[]
            )
        else:
            await f.create_folder(conn, user_id=user, name="하위", parent_id=r["id"])


async def test_scope_grants_membership_and_noop_audit(conn):
    group = await create_group(conn, "사업팀")
    r = await root(conn, visibility="private", grant_groups=["사업팀"])
    child = await f.create_folder(conn, user_id="kim", name="제안", parent_id=r["id"])
    assert await f.list_folders(conn, user_id="lee") == []
    await add_member(conn, group["id"], "lee")
    tree = await f.list_folders(conn, user_id="lee")
    assert {x["id"] for x in tree} == {r["id"], child["id"]}
    assert all(
        x["scope"] == {"visibility": "private", "users": [], "groups": ["사업팀"]} for x in tree
    )
    await remove_member(conn, group["id"], "lee")
    assert await f.list_folders(conn, user_id="lee") == []
    before = (
        await (
            await conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='folder_access_changed'"
            )
        ).fetchone()
    )[0]
    await f.set_folder_access(
        conn, r["id"], user_id="kim", visibility="private", users=[], groups=["사업팀", "사업팀"]
    )
    after = (
        await (
            await conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='folder_access_changed'"
            )
        ).fetchone()
    )[0]
    assert after == before
    assert await f.set_folder_access(
        conn, r["id"], user_id="kim", visibility="private", users=["lee"], groups=[]
    ) == {"visibility": "private", "users": ["lee"], "groups": []}
    assert len(await f.list_folders(conn, user_id="lee")) == 2
    await f.set_folder_access(
        conn, r["id"], user_id="kim", visibility="public", users=[], groups=[]
    )
    assert await f.get_folder_access(conn, r["id"], user_id="kim") == {
        "visibility": "public",
        "users": [],
        "groups": [],
    }
    await f.set_folder_access(
        conn, r["id"], user_id="kim", visibility="private", users=[], groups=[]
    )
    assert await f.list_folders(conn, user_id="lee") == []


async def test_subfolder_scope_and_duplicate_names(conn):
    r = await root(conn)
    child = await f.create_folder(conn, user_id="kim", name="채용", parent_id=r["id"])
    with pytest.raises(f.FolderNameTaken, match="같은 이름의 폴더가 이미 있습니다."):
        await f.create_folder(conn, user_id="lee", name="채용", parent_id=r["id"])
    sibling = await f.create_folder(conn, user_id="kim", name="지원", parent_id=r["id"])
    with pytest.raises(f.FolderNameTaken):
        await f.rename_folder(conn, sibling["id"], user_id="kim", is_admin=False, name="채용")
    for kwargs in [
        {"visibility": "public"},
        {"visibility": "private"},
        {"grant_users": []},
        {"grant_groups": ["사업팀"]},
    ]:
        with pytest.raises(f.SubfolderScope, match="하위 폴더는 상위 폴더의 열람 범위를 따릅니다."):
            await f.create_folder(conn, user_id="kim", name="다른", parent_id=r["id"], **kwargs)
    with pytest.raises(f.SubfolderScope):
        await f.set_folder_access(
            conn, child["id"], user_id="kim", visibility="private", users=[], groups=[]
        )
    await root(conn, visibility="private")
    assert (await f.create_folder(conn, user_id="lee", name="인사"))["id"] != r["id"]


async def test_find_folder_by_name_for_reimport(conn):
    """import 재실행이 다시 쓸 폴더 — 최상위는 이름이 유일하지 않아 만든 사람으로 좁히고 가장 오래된 것을,
    하위는 같은 부모·같은 이름 하나를 찾는다. 범위는 범위 요약과 같은 모양으로 함께 준다."""
    await create_group(conn, "사업팀")
    first = await root(conn, visibility="private", grant_groups=["사업팀"])
    await root(conn)
    await f.create_folder(conn, user_id="lee", name="인사")
    child = await f.create_folder(conn, user_id="kim", name="채용", parent_id=first["id"])

    found = await f.find_folder(conn, user_id="kim", name="인사", created_by="kim")
    assert found == {"id": first["id"], "visibility": "private", "users": [], "groups": ["사업팀"]}
    found = await f.find_folder(conn, user_id="kim", name="채용", parent_id=first["id"])
    assert found["id"] == child["id"]
    assert await f.find_folder(conn, user_id="kim", name="채용", created_by="kim") is None
    assert await f.find_folder(conn, user_id="kim", name="없음", created_by="kim") is None


async def test_find_folder_does_not_return_a_folder_the_user_cannot_see(conn):
    """볼 수 없는 폴더는 이름이 맞아도 없는 것이다 — 다른 경로가 재사용해도 존재가 새지 않는다 (ADR-027)."""
    hidden = await f.create_folder(conn, user_id="lee", name="비밀", visibility="private")
    child = await f.create_folder(conn, user_id="lee", name="채용", parent_id=hidden["id"])

    assert await f.find_folder(conn, user_id="kim", name="비밀") is None
    assert await f.find_folder(conn, user_id="kim", name="채용", parent_id=hidden["id"]) is None
    assert (await f.find_folder(conn, user_id="lee", name="비밀"))["id"] == hidden["id"]
    found = await f.find_folder(conn, user_id="lee", name="채용", parent_id=hidden["id"])
    assert found["id"] == child["id"]


async def test_direct_visible_document_count_and_nonempty_delete(conn):
    r = await root(conn)
    child = await f.create_folder(conn, user_id="kim", name="채용", parent_id=r["id"])
    for folder, follows in [(r, True), (r, False), (child, True)]:
        doc = await insert_test_document(
            conn, title="문서", content="내용", owner_id="kim", visibility="private"
        )
        await conn.execute(
            "UPDATE documents SET folder_id=%s, follows_folder=%s WHERE id=%s",
            (folder["id"], follows, doc),
        )
    tree = {x["id"]: x for x in await f.list_folders(conn, user_id="lee")}
    assert tree[r["id"]]["document_count"] == 1
    assert tree[child["id"]]["document_count"] == 1
    with pytest.raises(f.FolderNotEmpty, match="폴더가 비어 있지 않습니다."):
        await f.delete_folder(conn, r["id"], user_id="kim", is_admin=False)
    await conn.execute("DELETE FROM documents WHERE follows_folder")
    with pytest.raises(f.FolderNotEmpty):
        await f.delete_folder(conn, r["id"], user_id="kim", is_admin=False)
    await f.delete_folder(conn, child["id"], user_id="kim", is_admin=False)
    with pytest.raises(f.FolderNotEmpty):
        await f.delete_folder(conn, r["id"], user_id="admin", is_admin=True)
    await conn.execute("DELETE FROM documents")
    await f.delete_folder(conn, r["id"], user_id="kim", is_admin=False)
    assert await f.list_folders(conn, user_id="lee") == []


async def test_anonymous_share_validation_and_atomic_failure(conn):
    await root(conn, visibility="private")
    public = await f.create_folder(conn, user_id="kim", name="공개")
    anonymous = await f.list_folders(conn, user_id=None)
    assert [x["id"] for x in anonymous] == [public["id"]]
    assert anonymous[0]["can_manage"] is False
    assert anonymous[0]["can_change_access"] is False
    assert await f.list_folders(conn, user_id=f"share:{uuid4()}") == []
    with pytest.raises(UnknownGrantee):
        await f.create_folder(
            conn, user_id="kim", name="실패", visibility="private", grant_users=["missing"]
        )
    with pytest.raises(GrantsOnPublicDocument):
        await root(conn, grant_users=["lee"])
    for name in ["", "   ", "인사/채용"]:
        with pytest.raises(ValueError):
            await f.create_folder(conn, user_id="kim", name=name)
    assert len(await f.list_folders(conn, user_id="kim")) == 2


async def test_child_alone_prevents_delete(conn):
    r = await root(conn)
    await f.create_folder(conn, user_id="kim", name="하위", parent_id=r["id"])
    with pytest.raises(f.FolderNotEmpty, match="폴더가 비어 있지 않습니다."):
        await f.delete_folder(conn, r["id"], user_id="kim", is_admin=False)


async def test_unknown_access_target_is_atomic_and_missing_folder_is_hidden(conn):
    r = await root(conn, visibility="private", grant_users=["lee"])
    before = await f.get_folder_access(conn, r["id"], user_id="kim")
    with pytest.raises(UnknownGrantee):
        await f.set_folder_access(
            conn, r["id"], user_id="kim", visibility="private", users=[], groups=["missing"]
        )
    assert await f.get_folder_access(conn, r["id"], user_id="kim") == before
    with pytest.raises(f.FolderNotFound):
        await f.ensure_folder_visible(conn, uuid4(), user_id="kim")
