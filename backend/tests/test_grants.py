from datetime import datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services.auth import UserNotFound
from openarchive.services.grants import (
    GroupAlreadyExists,
    GroupNotFound,
    UnknownGrantee,
    add_member,
    create_group,
    delete_group,
    insert_grants,
    list_groups,
    list_principals,
    remove_member,
    resolve_grantees,
)
from openarchive.services.visibility import VISIBLE_TO_USER


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


async def add_user(conn, username):
    cur = await conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, 'unused') RETURNING id",
        (username,),
    )
    return (await cur.fetchone())[0]


async def test_create_group_trims_name_and_returns_empty_members(conn):
    group = await create_group(conn, "  인사팀  ")
    assert set(group) == {"id", "name", "created_at", "members"}
    assert isinstance(group["id"], UUID)
    assert isinstance(group["created_at"], datetime)
    assert group["name"] == "인사팀"
    assert group["members"] == []
    assert await list_groups(conn) == [group]
    with pytest.raises(GroupAlreadyExists):
        await create_group(conn, " 인사팀 ")


@pytest.mark.parametrize("name", ["", " \t\n "])
async def test_create_group_rejects_blank_names(conn, name):
    with pytest.raises(ValueError):
        await create_group(conn, name)
    assert await list_groups(conn) == []


async def test_groups_and_members_are_sorted_by_name(conn):
    z = await create_group(conn, "z-team")
    a = await create_group(conn, "a-team")
    for username in ["zoe", "amy"]:
        await add_user(conn, username)
        await add_member(conn, z["id"], username)
    z["members"] = ["amy", "zoe"]
    assert await list_groups(conn) == [a, z]


async def test_add_and_remove_member_are_idempotent(conn):
    group = await create_group(conn, "team")
    user_id = await add_user(conn, "alice")
    await add_member(conn, group["id"], "alice")
    await add_member(conn, group["id"], "alice")
    cur = await conn.execute("SELECT group_id, user_id FROM group_members")
    assert await cur.fetchall() == [(group["id"], user_id)]
    await remove_member(conn, group["id"], "alice")
    await remove_member(conn, group["id"], "alice")
    cur = await conn.execute("SELECT count(*) FROM group_members")
    assert await cur.fetchone() == (0,)


@pytest.mark.parametrize("operation", [add_member, remove_member])
async def test_membership_requires_existing_group_and_user(conn, operation):
    await add_user(conn, "alice")
    with pytest.raises(GroupNotFound):
        await operation(conn, uuid4(), "alice")
    group = await create_group(conn, "team")
    with pytest.raises(UserNotFound):
        await operation(conn, group["id"], "missing")


async def test_delete_group_cascades_only_its_grants_and_members(conn):
    group = await create_group(conn, "team")
    other = await create_group(conn, "other")
    user_id = await add_user(conn, "alice")
    await add_member(conn, group["id"], "alice")
    doc = await insert_test_document(conn, title="문서", content="텍스트", visibility="private")
    await insert_grants(conn, doc, [user_id], [group["id"], other["id"]])
    await delete_group(conn, group["id"])
    assert await list_groups(conn) == [other]
    cur = await conn.execute("SELECT user_id, group_id FROM document_grants")
    assert set(await cur.fetchall()) == {(user_id, None), (None, other["id"])}
    cur = await conn.execute("SELECT count(*) FROM group_members")
    assert await cur.fetchone() == (0,)
    with pytest.raises(GroupNotFound):
        await delete_group(conn, group["id"])


async def test_list_principals_returns_sorted_names_except_viewer(conn):
    assert await list_principals(conn, viewer="amy") == {"users": [], "groups": []}
    for name in ["zoe", "amy", "bob"]:
        await add_user(conn, name)
    for name in ["z-team", "a-team"]:
        await create_group(conn, name)
    assert await list_principals(conn, viewer="amy") == {
        "users": ["bob", "zoe"], "groups": ["a-team", "z-team"]
    }


async def test_resolve_grantees_deduplicates_names(conn):
    alice = await add_user(conn, "alice")
    bob = await add_user(conn, "bob")
    group = await create_group(conn, "team")
    users, groups = await resolve_grantees(
        conn, users=["bob", "alice", "bob"], groups=["team", "team"]
    )
    assert len(users) == 2
    assert set(users) == {alice, bob}
    assert groups == [group["id"]]
    assert await resolve_grantees(conn, users=[], groups=[]) == ([], [])


@pytest.mark.parametrize(
    ("kind", "label", "names"),
    [("user", "사용자", ["bob", "carol"]), ("group", "그룹", ["인사팀", "재무팀"])],
)
async def test_resolve_reports_all_unknown_names(conn, kind, label, names):
    await add_user(conn, "known")
    await create_group(conn, "known")
    requested = ["known", *names, names[0]]
    with pytest.raises(UnknownGrantee) as exc:
        await resolve_grantees(
            conn,
            users=requested if kind == "user" else ["known"],
            groups=requested if kind == "group" else ["known"],
        )
    assert exc.value.kind == kind
    assert exc.value.names == names
    assert str(exc.value) == f"알 수 없는 {label}: {', '.join(names)}"


async def test_insert_grants_deduplicates_both_kinds(conn):
    user_ids = [await add_user(conn, name) for name in ["alice", "bob"]]
    group_ids = [(await create_group(conn, name))["id"] for name in ["a", "b"]]
    doc = await insert_test_document(conn, title="문서", content="텍스트", visibility="private")
    await insert_grants(conn, doc, [], [])
    for _ in range(2):
        await insert_grants(conn, doc, user_ids + user_ids, group_ids + group_ids)
    cur = await conn.execute("SELECT document_id, user_id, group_id FROM document_grants")
    rows = await cur.fetchall()
    assert len(rows) == 4
    assert set(rows) == {
        *((doc, user_id, None) for user_id in user_ids),
        *((doc, None, group_id) for group_id in group_ids),
    }


async def test_removing_member_immediately_hides_group_grant(conn):
    await add_user(conn, "bob")
    group = await create_group(conn, "team")
    await add_member(conn, group["id"], "bob")
    doc = await insert_test_document(conn, title="제한", content="텍스트", visibility="private")
    user_ids, group_ids = await resolve_grantees(conn, users=[], groups=["team"])
    await insert_grants(conn, doc, user_ids, group_ids)
    sql = f"SELECT d.id FROM documents d WHERE {VISIBLE_TO_USER}"
    cur = await conn.execute(sql, {"user": "bob"})
    assert await cur.fetchall() == [(doc,)]
    await remove_member(conn, group["id"], "bob")
    cur = await conn.execute(sql, {"user": "bob"})
    assert await cur.fetchall() == []
