"""폴더 열람 범위 상속 (ADR-054, #187).

범위는 최상위 폴더만 갖고 하위 폴더·「폴더 범위 따름」 문서는 그것을 따른다. 「개별 지정」
문서는 문서 자신의 범위로 판정한다. 판정은 조회 시점이라 폴더 부여·그룹 구성원 변경이 문서를
고치지 않고 바로 반영된다. 술어 하나(`VISIBLE_TO_USER`)를 서비스 목록과 SQL 양쪽으로 확인한다.

폴더 안 문서의 소유자는 writer로 둔다 — 소유자 분기와 폴더 분기를 섞지 않기 위해서다.
"""

import psycopg
import pytest
from conftest import insert_test_document

from openarchive.services.documents import list_documents
from openarchive.services.visibility import (
    FOLDER_VISIBLE_TO_USER,
    VISIBLE_TO_USER,
    share_principal,
)

USERS = ["kim", "lee", "park", "boss", "writer", "admin"]


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


async def scalar(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return (await cur.fetchone())[0]


async def add_folder(conn, name, created_by="boss", parent=None, visibility=None):
    if parent is None and visibility is None:
        visibility = "public"
    return await scalar(
        conn,
        """
        INSERT INTO folders (parent_id, name, created_by, visibility)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (parent, name, created_by, visibility),
    )


async def grant_folder_group(conn, folder_id, group_id):
    await conn.execute(
        "INSERT INTO folder_grants (folder_id, group_id) VALUES (%s, %s)", (folder_id, group_id)
    )


async def add_doc(conn, title, *, folder=None, follows=True, visibility="private",
                  owner="writer"):
    document_id = await insert_test_document(
        conn, title=title, content=f"{title} 본문", owner_id=owner, visibility=visibility
    )
    await conn.execute(
        "UPDATE documents SET folder_id = %s, follows_folder = %s WHERE id = %s",
        (folder, follows, document_id),
    )
    return document_id


async def sql_visible(conn, user):
    cur = await conn.execute(
        f"SELECT d.title FROM documents d WHERE {VISIBLE_TO_USER}", {"user": user}
    )
    return {row[0] for row in await cur.fetchall()}


async def visible(conn, user):
    """서비스 목록과 SQL 술어가 같은 집합을 내는지 확인하고 돌려준다."""
    titles = {row["title"] for row in await list_documents(conn, user_id=user)}
    assert titles == await sql_visible(conn, user)
    return titles


async def visible_folders(conn, user):
    cur = await conn.execute(
        f"SELECT f.name FROM folders f WHERE {FOLDER_VISIBLE_TO_USER}", {"user": user}
    )
    return {row[0] for row in await cur.fetchall()}


@pytest.fixture
async def org(conn):
    ids = {}
    for username in USERS:
        ids[username] = await scalar(
            conn,
            "INSERT INTO users (username, password_hash, is_admin) "
            "VALUES (%s, 'unused', %s) RETURNING id",
            (username, username == "admin"),
        )
    groups = {}
    for name, members in {"사업팀": ["kim", "park"], "개발팀": ["lee", "park"]}.items():
        groups[name] = await scalar(
            conn, "INSERT INTO groups (name) VALUES (%s) RETURNING id", (name,)
        )
        for member in members:
            await conn.execute(
                "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)",
                (groups[name], ids[member]),
            )
    rfp = await add_folder(conn, "RFP", visibility="private")
    await grant_folder_group(conn, rfp, groups["사업팀"])
    y2026 = await add_folder(conn, "2026", parent=rfp)
    q1 = await add_folder(conn, "1분기", parent=y2026)
    return {"users": ids, "groups": groups, "rfp": rfp, "y2026": y2026, "q1": q1}


async def test_document_without_folder_keeps_its_own_scope(conn, org):
    await add_doc(conn, "공개", visibility="public")
    await add_doc(conn, "제한")
    assert await visible(conn, "lee") == {"공개"}
    assert await visible(conn, "writer") == {"공개", "제한"}
    assert await visible(conn, None) == {"공개"}


async def test_private_root_folder_scope_is_inherited(conn, org):
    await add_doc(conn, "제안서", folder=org["rfp"])
    for user in ["kim", "park", "boss", "writer"]:
        assert "제안서" in await visible(conn, user), user
    for user in ["lee", "admin", None]:
        assert "제안서" not in await visible(conn, user), user


async def test_deep_subfolder_follows_root_scope(conn, org):
    await add_doc(conn, "깊은 제안서", folder=org["q1"])
    for user in ["kim", "park", "boss", "writer"]:
        assert "깊은 제안서" in await visible(conn, user), user
    for user in ["lee", "admin", None]:
        assert "깊은 제안서" not in await visible(conn, user), user


async def test_folder_grant_change_applies_without_touching_documents(conn, org):
    await add_doc(conn, "제안서", folder=org["q1"])
    assert "제안서" not in await visible(conn, "lee")
    await grant_folder_group(conn, org["rfp"], org["groups"]["개발팀"])
    assert "제안서" in await visible(conn, "lee")
    await conn.execute(
        "DELETE FROM folder_grants WHERE folder_id = %s AND group_id = %s",
        (org["rfp"], org["groups"]["개발팀"]),
    )
    assert "제안서" not in await visible(conn, "lee")


async def test_direct_user_grant_on_folder(conn, org):
    await add_doc(conn, "제안서", folder=org["rfp"])
    await conn.execute(
        "INSERT INTO folder_grants (folder_id, user_id) VALUES (%s, %s)",
        (org["rfp"], org["users"]["lee"]),
    )
    assert "제안서" in await visible(conn, "lee")


async def test_group_membership_change_applies_immediately(conn, org):
    await add_doc(conn, "제안서", folder=org["y2026"])
    await conn.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)",
        (org["groups"]["사업팀"], org["users"]["lee"]),
    )
    assert "제안서" in await visible(conn, "lee")
    await conn.execute(
        "DELETE FROM group_members WHERE group_id = %s AND user_id = %s",
        (org["groups"]["사업팀"], org["users"]["lee"]),
    )
    assert "제안서" not in await visible(conn, "lee")


async def test_individually_scoped_document_ignores_folder(conn, org):
    document_id = await add_doc(conn, "인사 메모", folder=org["rfp"], follows=False)
    assert "인사 메모" in await visible(conn, "writer")
    for user in ["boss", "kim", "park", "lee", "admin"]:
        assert "인사 메모" not in await visible(conn, user), user
    await conn.execute(
        "UPDATE documents SET follows_folder = true WHERE id = %s", (document_id,)
    )
    for user in ["kim", "park", "boss"]:
        assert "인사 메모" in await visible(conn, user), user
    assert "인사 메모" not in await visible(conn, "lee")


async def test_individually_public_document_in_hidden_folder_is_visible(conn, org):
    audit = await add_folder(conn, "감사팀", visibility="private")
    await add_doc(conn, "공개 공지", folder=audit, follows=False, visibility="public")
    assert "공개 공지" in await visible(conn, "lee")
    assert "감사팀" not in await visible_folders(conn, "lee")


async def test_owner_always_sees_own_document_in_hidden_folder(conn, org):
    audit = await add_folder(conn, "감사팀", visibility="private")
    await add_doc(conn, "내 보고", folder=audit, owner="lee")
    assert "내 보고" in await visible(conn, "lee")
    assert "내 보고" not in await visible(conn, "kim")


async def test_public_folder_wins_over_document_private_visibility(conn, org):
    open_folder = await add_folder(conn, "공지", visibility="public")
    sub = await add_folder(conn, "사내", parent=open_folder)
    await add_doc(conn, "사내 공지", folder=sub, visibility="private")
    for user in ["kim", "lee", "park", "admin", None]:
        assert "사내 공지" in await visible(conn, user), user


async def test_share_principal_ignores_folder_scope(conn, org):
    open_folder = await add_folder(conn, "공지", visibility="public")
    await add_doc(conn, "공개 폴더 문서", folder=open_folder)
    shared = await add_doc(conn, "공유된 제안서", folder=org["rfp"])
    share_id = await scalar(
        conn,
        "INSERT INTO shares (owner_user_id, name) VALUES (%s, '외부') RETURNING id",
        (org["users"]["writer"],),
    )
    await conn.execute(
        "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)",
        (shared, share_id),
    )
    assert await visible(conn, share_principal(share_id)) == {"공유된 제안서"}
    assert await visible_folders(conn, share_principal(share_id)) == set()


async def test_anonymous_sees_only_public_folder_documents(conn, org):
    open_folder = await add_folder(conn, "공지", visibility="public")
    await add_doc(conn, "공개 폴더 문서", folder=open_folder)
    await add_doc(conn, "제한 폴더 문서", folder=org["q1"])
    assert await visible(conn, None) == {"공개 폴더 문서"}
    assert await visible_folders(conn, None) == {"공지"}


async def test_folder_predicate_follows_root_scope(conn, org):
    await add_folder(conn, "공지", visibility="public")
    rfp_tree = {"RFP", "2026", "1분기"}
    for user in ["kim", "park", "boss"]:
        assert await visible_folders(conn, user) == rfp_tree | {"공지"}, user
    for user in ["lee", "admin", "writer"]:
        assert await visible_folders(conn, user) == {"공지"}, user

    await grant_folder_group(conn, org["rfp"], org["groups"]["개발팀"])
    assert await visible_folders(conn, "lee") == rfp_tree | {"공지"}
    await conn.execute(
        "INSERT INTO group_members (group_id, user_id) VALUES (%s, %s)",
        (org["groups"]["개발팀"], org["users"]["admin"]),
    )
    assert await visible_folders(conn, "admin") == rfp_tree | {"공지"}
