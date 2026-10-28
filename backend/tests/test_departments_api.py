# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""부서 API의 세션 경계와 위임 관리."""
from uuid import uuid4

import psycopg
import pytest
from conftest import login_as
from test_groups_api import create_group, ensure_user, login_admin

WRITES = [("patch", "", {"description": "설명"}), ("put", "/members/target", None),
          ("delete", "/members/target", None), ("put", "/managers/target", None),
          ("delete", "/managers/target", None)]


@pytest.fixture
def department(db_client, migrated_db):
    login_admin(db_client, migrated_db)
    group = create_group(db_client)
    for name in ["manager", "member", "target"]:
        ensure_user(migrated_db, name)
        assert db_client.put(f"/api/admin/groups/{group['id']}/members/{name}").status_code == 204
    with psycopg.connect(migrated_db) as conn:
        conn.execute("INSERT INTO group_managers SELECT %s, id FROM users WHERE username='manager'",
                     (group["id"],))
    return group["id"]


def call(client, method, path, body):
    return client.request(method, path, **({"json": body} if body is not None else {}))


def test_directory(db_client, department):
    login_as(db_client, "member")
    response = db_client.get("/api/departments")
    assert response.status_code == 200
    row = response.json()[0]
    assert row == {"id": department, "name": "인사팀", "description": "", "folder_id": None,
                   "members": [{"username": "manager", "manager": True, "primary": False},
                               {"username": "member", "manager": False, "primary": False},
                               {"username": "target", "manager": False, "primary": False}],
                   "granted_document_count": 0, "can_manage": False}
    token = db_client.post("/api/auth/tokens", json={"name": "read"}).json()["token"]
    db_client.cookies.clear()
    assert db_client.get("/api/departments").status_code == 401
    assert db_client.get("/api/departments", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_share_rejected(db_client, department):
    share = db_client.post("/api/shares", json={"name": "외부"}).json()
    token = db_client.post(f"/api/shares/{share['id']}/tokens", json={"name": "외부"}).json()["token"]
    response = db_client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "suffix", "body"), WRITES)
def test_session_and_management_boundary(db_client, department, method, suffix, body):
    path = f"/api/departments/{department}{suffix}"
    db_client.cookies.clear()
    assert call(db_client, method, path, body).status_code == 401
    login_as(db_client, "boss")
    token = db_client.post("/api/auth/tokens", json={"name": "write", "scope": "read_write"}).json()["token"]
    response = db_client.request(method, path, headers={"Authorization": f"Bearer {token}"},
                                 **({"json": body} if body else {}))
    assert response.status_code == 403
    assert response.json()["detail"] == "로그인 세션이 필요합니다."
    login_as(db_client, "member")
    denied = call(db_client, method, path, body)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "부서 관리자 권한이 필요합니다."
    login_as(db_client, "manager")
    assert call(db_client, method, path, body).status_code == 204
    login_as(db_client, "boss")
    assert call(db_client, method, path, body).status_code == 204


def test_errors_and_audit(db_client, migrated_db, department):
    other = create_group(db_client, "다른 부서")["id"]
    login_as(db_client, "manager")
    base = f"/api/departments/{department}"
    assert db_client.patch(f"/api/departments/{other}", json={"description": ""}).status_code == 403
    missing = db_client.patch(f"/api/departments/{uuid4()}", json={"description": ""})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "그룹을 찾을 수 없습니다."
    for method in ["put", "delete"]:
        for kind in ["members", "managers"]:
            response = db_client.request(method, f"{base}/{kind}/nobody")
            assert response.status_code == 404
            assert response.json()["detail"] == "사용자를 찾을 수 없습니다."
    ensure_user(migrated_db, "outsider")
    response = db_client.put(f"{base}/managers/outsider")
    assert response.status_code == 400
    assert response.json()["detail"] == "부서 구성원만 부서 관리자가 될 수 있습니다."
    assert db_client.patch(base, json={"description": "x" * 201}).status_code == 422
    assert db_client.patch(base, json={"description": "새 설명"}).status_code == 204
    assert db_client.get("/api/departments").json()[0]["description"] == "새 설명"
    assert db_client.put(f"{base}/members/outsider").status_code == 204
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute("SELECT actor, actor_via, detail FROM audit_log "
                           "WHERE action='group_member_changed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row == ("manager", "session", {"change": "added", "group": "인사팀", "user": "outsider"})


def test_manager_changes_are_persisted(db_client, department):
    login_as(db_client, "manager")
    base = f"/api/departments/{department}"
    assert db_client.put(f"{base}/managers/target").status_code == 204
    members = db_client.get("/api/departments").json()[0]["members"]
    assert next(m for m in members if m["username"] == "target")["manager"] is True
    assert db_client.delete(f"{base}/managers/target").status_code == 204
    members = db_client.get("/api/departments").json()[0]["members"]
    assert next(m for m in members if m["username"] == "target")["manager"] is False
    assert db_client.delete(f"{base}/members/target").status_code == 204
    members = db_client.get("/api/departments").json()[0]["members"]
    assert "target" not in [m["username"] for m in members]
    assert db_client.put(f"{base}/members/target").status_code == 204
    members = db_client.get("/api/departments").json()[0]["members"]
    assert "target" in [m["username"] for m in members]
