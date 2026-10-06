"""폴더 REST 계약과 세션·토큰·공유 경계."""

import psycopg
import pytest
from conftest import login_as
from test_groups_api import login_admin
from test_share_access import issue_share_token
from test_token_access import bearer, issue_token

MISSING = "00000000-0000-0000-0000-000000000000"


def folder(client, name="자료", parent_id=None, headers=None):
    response = client.post(
        "/api/folders", json={"name": name, "parent_id": parent_id}, headers=headers
    )
    assert response.status_code == 201
    return response.json()


def test_folder_lifecycle_and_inherited_scope(db_client):
    login_as(db_client, "alice")
    root = folder(db_client)
    assert root == {
        "id": root["id"],
        "parent_id": None,
        "name": "자료",
        "created_by": "alice",
        "document_count": 0,
        "scope": {"visibility": "public", "users": [], "groups": []},
        "inherited": False,
        "can_manage": True,
        "can_change_access": True,
    }
    child = folder(db_client, "하위", root["id"])
    assert child["inherited"] is True
    assert child["scope"] == root["scope"]
    assert child["can_change_access"] is False
    listed = db_client.get("/api/folders")
    assert listed.status_code == 200
    assert listed.json() == [root, child]
    renamed = db_client.patch(f"/api/folders/{child['id']}", json={"name": "변경"})
    assert renamed.status_code == 200
    assert renamed.json() == {**child, "name": "변경"}
    assert db_client.get(f"/api/folders/{root['id']}/access").json() == root["scope"]
    nonempty = db_client.delete(f"/api/folders/{root['id']}")
    assert nonempty.status_code == 409
    assert nonempty.json()["detail"] == "폴더가 비어 있지 않습니다."
    assert db_client.delete(f"/api/folders/{child['id']}").status_code == 204
    deleted = db_client.delete(f"/api/folders/{root['id']}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert db_client.get("/api/folders").json() == []


def test_duplicate_child_and_invalid_names(db_client):
    login_as(db_client, "alice")
    root = folder(db_client)
    child = folder(db_client, "중복", root["id"])
    other = folder(db_client, "다른", root["id"])
    for response in (
        db_client.post("/api/folders", json={"name": "중복", "parent_id": root["id"]}),
        db_client.patch(f"/api/folders/{other['id']}", json={"name": child["name"]}),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == "같은 이름의 폴더가 이미 있습니다."
    for name in (" ", "a/b"):
        for response in (
            db_client.post("/api/folders", json={"name": name}),
            db_client.patch(f"/api/folders/{other['id']}", json={"name": name}),
        ):
            assert response.status_code == 400
            assert (
                response.json()["detail"]
                == "폴더 이름은 공백이 아니어야 하며 /를 포함할 수 없습니다."
            )


def test_access_grants_and_audit_actor(db_client, migrated_db):
    login_as(db_client, "bob")
    login_admin(db_client, migrated_db)
    assert db_client.post("/api/admin/groups", json={"name": "팀"}).status_code == 201
    login_as(db_client, "alice")
    root = folder(db_client)
    scope = {"visibility": "private", "users": ["bob"], "groups": ["팀"]}
    saved = db_client.put(f"/api/folders/{root['id']}/access", json=scope)
    assert saved.status_code == 200 and saved.json() == scope
    assert db_client.get(f"/api/folders/{root['id']}/access").json() == scope
    assert db_client.get("/api/folders").json()[0]["scope"] == scope
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT actor, actor_via FROM audit_log WHERE action='folder_access_changed'"
        ).fetchall()
    assert rows and all(row == ("alice", "session") for row in rows)
    login_as(db_client, "bob")
    visible = db_client.get("/api/folders").json()[0]
    assert visible["can_manage"] is False and visible["can_change_access"] is False
    assert db_client.get(f"/api/folders/{root['id']}/access").status_code == 403
    assert folder(db_client, "공동", root["id"])["created_by"] == "bob"


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("GET", "/access", None),
        ("PUT", "/access", {"visibility": "private"}),
        ("PATCH", "", {"name": "이름 변경"}),
        ("DELETE", "", None),
    ],
)
def test_noncreator_forbidden_and_hidden_folder_not_found(db_client, method, suffix, body):
    login_as(db_client, "alice")
    root = folder(db_client)
    login_as(db_client, "bob")
    denied = db_client.request(method, f"/api/folders/{root['id']}{suffix}", json=body)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "폴더를 관리할 권한이 없습니다."
    login_as(db_client, "alice")
    assert (
        db_client.put(
            f"/api/folders/{root['id']}/access", json={"visibility": "private"}
        ).status_code
        == 200
    )
    login_as(db_client, "bob")
    hidden = db_client.request(method, f"/api/folders/{root['id']}{suffix}", json=body)
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "폴더를 찾을 수 없습니다."
    assert db_client.get("/api/folders").json() == []
    parent = db_client.post("/api/folders", json={"name": "하위", "parent_id": root["id"]})
    assert parent.status_code == 404 and parent.json() == hidden.json()


def test_admin_manage_without_access_override(db_client, migrated_db):
    login_as(db_client, "alice")
    root = folder(db_client)
    hidden = folder(db_client, "비밀")
    assert (
        db_client.put(
            f"/api/folders/{hidden['id']}/access", json={"visibility": "private"}
        ).status_code
        == 200
    )
    login_admin(db_client, migrated_db)
    listed = db_client.get("/api/folders").json()
    assert len(listed) == 1 and listed[0]["can_manage"] is True
    assert listed[0]["can_change_access"] is False
    assert db_client.patch(f"/api/folders/{root['id']}", json={"name": "관리"}).status_code == 200
    assert (
        db_client.put(
            f"/api/folders/{root['id']}/access", json={"visibility": "private"}
        ).status_code
        == 403
    )
    assert db_client.get(f"/api/folders/{root['id']}/access").status_code == 403
    assert db_client.patch(f"/api/folders/{hidden['id']}", json={"name": "관리"}).status_code == 404
    assert db_client.delete(f"/api/folders/{hidden['id']}").status_code == 404
    assert db_client.delete(f"/api/folders/{root['id']}").status_code == 204


def test_child_scope_rejected(db_client):
    login_as(db_client, "alice")
    root = folder(db_client)
    child = folder(db_client, parent_id=root["id"])
    for method in ("GET", "PUT"):
        response = db_client.request(
            method, f"/api/folders/{child['id']}/access", json={"visibility": "public"}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "하위 폴더는 상위 폴더의 열람 범위를 따릅니다."


def test_write_token_manages_folders_but_cannot_change_access(db_client):
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    db_client.cookies.clear()
    headers = bearer(token)
    root = folder(db_client, headers=headers)
    assert (
        db_client.patch(
            f"/api/folders/{root['id']}", headers=headers, json={"name": "토큰"}
        ).status_code
        == 200
    )
    assert db_client.get("/api/folders", headers=headers).status_code == 200
    assert db_client.get(f"/api/folders/{root['id']}/access", headers=headers).status_code == 200
    response = db_client.put(
        f"/api/folders/{root['id']}/access", headers=headers, json={"visibility": "private"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "로그인 세션이 필요합니다."
    doc = db_client.post(
        "/api/documents/text", headers=headers, json={"title": "자료", "content": "본문"}
    ).json()
    moved = db_client.put(
        f"/api/documents/{doc['id']}/folder", headers=headers, json={"folder_id": root["id"]}
    )
    assert moved.status_code == 200 and moved.json()["folder"]["id"] == root["id"]
    assert (
        db_client.put(
            f"/api/documents/{doc['id']}/folder", headers=headers, json={"folder_id": None}
        ).status_code
        == 200
    )
    assert db_client.delete(f"/api/folders/{root['id']}", headers=headers).status_code == 204


def test_share_rejects_every_folder_route_and_hides_metadata(db_client):
    login_as(db_client, "alice")
    root = folder(db_client)
    doc = db_client.post(
        "/api/documents/text", json={"title": "공유", "content": "본문", "folder_id": root["id"]}
    ).json()
    share = db_client.post("/api/shares", json={"name": "외부"}).json()
    assert db_client.put(f"/api/shares/{share['id']}/documents/{doc['id']}").status_code == 204
    token = issue_share_token(db_client, share["id"])["token"]
    db_client.cookies.clear()
    headers = bearer(token)
    for method, path, body in (
        ("GET", "", None),
        ("POST", "", {"name": "공유"}),
        ("PATCH", f"/{root['id']}", {"name": "수정"}),
        ("DELETE", f"/{root['id']}", None),
        ("GET", f"/{root['id']}/access", None),
        ("PUT", f"/{root['id']}/access", {"visibility": "public"}),
    ):
        response = db_client.request(method, f"/api/folders{path}", headers=headers, json=body)
        assert response.status_code == 403
        assert response.json()["detail"] == "공유 토큰으로는 열 수 없는 경로입니다."
    assert (
        db_client.get("/api/documents", params={"folder_id": root["id"]}, headers=headers).json()
        == []
    )
    assert db_client.get(
        "/api/documents/count", params={"folder_id": root["id"]}, headers=headers
    ).json() == {"total": 0}
    detail = db_client.get(f"/api/documents/{doc['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json()["folder"] is None


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "", None),
        ("POST", "", {"name": "자료"}),
        ("PATCH", f"/{MISSING}", {"name": "수정"}),
        ("DELETE", f"/{MISSING}", None),
        ("GET", f"/{MISSING}/access", None),
        ("PUT", f"/{MISSING}/access", {"visibility": "public"}),
    ],
)
def test_folder_authentication_required(db_client, method, path, body):
    response = db_client.request(method, f"/api/folders{path}", json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body,detail",
    [
        ({"visibility": "public", "users": ["bob"]}, "visibility=private"),
        ({"visibility": "private", "users": ["ghost"]}, "ghost"),
        ({"visibility": "private", "groups": ["없는팀"]}, "없는팀"),
        ({"visibility": "private", "users": ["alice"]}, "소유자"),
    ],
)
def test_invalid_folder_grants_do_not_change_scope(db_client, body, detail):
    login_as(db_client, "alice")
    root = folder(db_client)
    response = db_client.put(f"/api/folders/{root['id']}/access", json=body)
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert db_client.get(f"/api/folders/{root['id']}/access").json() == root["scope"]


def test_read_token_cannot_write_folders(db_client):
    token = issue_token(db_client, "alice")["token"]
    root = folder(db_client)
    db_client.cookies.clear()
    headers = bearer(token)
    for method, path, body in (
        ("POST", "", {"name": "새 폴더"}),
        ("PATCH", f"/{root['id']}", {"name": "수정"}),
        ("DELETE", f"/{root['id']}", None),
    ):
        response = db_client.request(method, f"/api/folders{path}", headers=headers, json=body)
        assert response.status_code == 403
        assert response.json()["detail"] == "쓰기 권한이 필요합니다."
    assert db_client.get("/api/folders", headers=headers).status_code == 200
