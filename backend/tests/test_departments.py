# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""부서 조회와 위임 관리의 실제 DB 계약."""

import psycopg
import pytest

from openarchive.services import departments as service
from openarchive.services import grants


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        for name in ["owner", "manager", "member", "viewer"]:
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, 'unused')", (name,)
            )
        yield conn


async def test_directory_members_and_management_flags(conn):
    group = await grants.create_group(conn, "A")
    await grants.create_group(conn, "B")
    for name in ["member", "manager"]:
        await grants.add_member(conn, group["id"], name)
    await conn.execute(
        "INSERT INTO group_managers SELECT %s, id FROM users WHERE username='manager'",
        (group["id"],),
    )
    await conn.execute(
        "UPDATE users SET department_group_id=%s WHERE username='member'", (group["id"],)
    )
    rows = await service.list_departments(conn, user_id="viewer", is_admin=False)
    assert [r["name"] for r in rows] == ["A", "B"]
    assert rows[0]["members"] == [
        {"username": "manager", "manager": True, "primary": False},
        {"username": "member", "manager": False, "primary": True},
    ]
    assert rows[1]["members"] == []
    assert rows[0]["description"] == ""
    assert not rows[0]["can_manage"]
    assert (await service.list_departments(conn, user_id="manager", is_admin=False))[0]["can_manage"]
    assert all(r["can_manage"] for r in
               await service.list_departments(conn, user_id="viewer", is_admin=True))


@pytest.mark.parametrize("operation", ["add", "remove", "manager", "description"])
async def test_management_boundary_and_missing_group(conn, operation):
    from uuid import uuid4

    async def perform(group_id, user, admin=False):
        kwargs = {"user_id": user, "is_admin": admin}
        if operation == "add":
            await service.add_department_member(conn, group_id, "viewer", **kwargs)
        elif operation == "remove":
            await service.remove_department_member(conn, group_id, "viewer", **kwargs)
        elif operation == "manager":
            await service.set_department_manager(conn, group_id, "member", True, **kwargs)
        else:
            result = await service.update_department(
                conn, group_id, description="  설명  ", **kwargs
            )
            assert result["description"] == "설명"

    group = await grants.create_group(conn, "A")
    other = await grants.create_group(conn, "B")
    for name in ["manager", "member"]:
        await grants.add_member(conn, group["id"], name)
    await conn.execute(
        "INSERT INTO group_managers SELECT %s, id FROM users WHERE username='manager'",
        (group["id"],),
    )
    with pytest.raises(grants.GroupNotFound):
        await perform(uuid4(), "member")
    with pytest.raises(service.NotDepartmentManager):
        await perform(group["id"], "member")
    with pytest.raises(service.NotDepartmentManager):
        await perform(other["id"], "manager")
    await perform(group["id"], "manager")
    await perform(group["id"], "manager")  # 멱등
    await perform(group["id"], "viewer", True)


async def test_manager_membership_cascade_description_and_audit(conn):
    from openarchive.services.audit import list_audit, set_actor

    group = await grants.create_group(conn, "A")
    await grants.add_member(conn, group["id"], "manager")
    await service.set_department_manager(
        conn, group["id"], "manager", True, user_id="owner", is_admin=True
    )
    kwargs = {"user_id": "manager", "is_admin": False}
    with pytest.raises(service.ManagerMustBeMember):
        await service.set_department_manager(conn, group["id"], "viewer", True, **kwargs)
    async with conn.transaction():
        await set_actor(conn, actor="manager", via="session")
        await service.add_department_member(conn, group["id"], "viewer", **kwargs)
    records = await list_audit(conn, actor="manager", action="group_member_changed")
    assert len(records) == 1
    assert records[0]["actor_via"] == "session"
    assert records[0]["detail"] == {"change": "added", "group": "A", "user": "viewer"}
    await service.set_department_manager(conn, group["id"], "viewer", True, **kwargs)
    await service.set_department_manager(conn, group["id"], "viewer", False, **kwargs)
    await service.set_department_manager(conn, group["id"], "viewer", False, **kwargs)
    with pytest.raises(ValueError):
        await service.update_department(conn, group["id"], description="x" * 201, **kwargs)
    await service.update_department(conn, group["id"], description="  " + "x" * 200, **kwargs)
    assert (await service.list_departments(conn, **kwargs))[0]["description"] == "x" * 200
    await service.remove_department_member(conn, group["id"], "manager", **kwargs)
    with pytest.raises(service.NotDepartmentManager):
        await service.ensure_can_manage(conn, group["id"], **kwargs)


async def test_directory_filters_folder_and_granted_documents_by_visibility(conn):
    from conftest import insert_test_document

    from openarchive.services import folders
    from openarchive.services.documents import DocumentNotFound, ensure_visible
    from openarchive.services.trash import trash_document

    group = await grants.create_group(conn, "A")
    await grants.add_member(conn, group["id"], "manager")
    await service.set_department_manager(
        conn, group["id"], "manager", True, user_id="owner", is_admin=True
    )
    folder = await folders.create_folder(conn, user_id="owner", name="비공개")
    await folders.set_folder_access(
        conn, folder["id"], user_id="owner", visibility="private", users=[], groups=[]
    )
    # 폴더 지정 조건을 만족하는 소유자에게 그룹 자격을 주고 부서 폴더로 지정한다.
    await grants.add_member(conn, group["id"], "owner")
    await folders.set_folder_access(
        conn, folder["id"], user_id="owner", visibility="private", users=[], groups=["A"]
    )
    await folders.set_folder_department(
        conn, folder["id"], user_id="owner", group_id=group["id"]
    )
    visible = await insert_test_document(
        conn, title="제한", content="문서 텍스트", owner_id="owner", visibility="private"
    )
    trashed = await insert_test_document(
        conn, title="제한", content="문서 텍스트", owner_id="owner", visibility="private"
    )
    hidden = await insert_test_document(
        conn, title="제한", content="문서 텍스트", owner_id="owner", visibility="private"
    )
    for document_id in [visible, trashed, hidden]:
        await grants.insert_grants(conn, document_id, [], [group["id"]])
    await trash_document(conn, trashed, user_id="owner")
    # 문서 자신의 그룹 부여가 있어도 폴더 범위를 따르는 문서는 폴더로 판정한다.
    secret = await folders.create_folder(conn, user_id="owner", name="별도")
    await folders.set_folder_access(
        conn, secret["id"], user_id="owner", visibility="private", users=[], groups=[]
    )
    await conn.execute("UPDATE documents SET folder_id=%s WHERE id=%s", (secret["id"], hidden))
    unrelated = await insert_test_document(
        conn, title="제한", content="문서 텍스트", owner_id="owner", visibility="private"
    )
    rows = await service.list_departments(conn, user_id="manager", is_admin=False)
    assert rows[0]["folder_id"] == folder["id"]
    assert rows[0]["granted_document_count"] == 1
    with pytest.raises(DocumentNotFound):
        await ensure_visible(conn, unrelated, user_id="manager")
    rows = await service.list_departments(conn, user_id="viewer", is_admin=True)
    assert rows[0]["folder_id"] is None
    assert rows[0]["granted_document_count"] == 0
