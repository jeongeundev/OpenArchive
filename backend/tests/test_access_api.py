"""문서 열람 범위 API와 생성 시 부여 대상 인자 (ADR-044 관리 경로)."""

import psycopg
import pytest
from conftest import login_as, upload_document
from fastapi.testclient import TestClient
from test_groups_api import create_group, login_admin
from test_token_access import bearer, issue_token

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def make_group(client: TestClient, dsn: str, name: str = "인사팀") -> None:
    """관리자 세션으로 그룹을 만든다. 끝나면 쿠키는 관리자 것이다."""
    login_admin(client, dsn)
    create_group(client, name)


def ensure_users(client: TestClient, *names: str) -> None:
    for name in names:
        login_as(client, name)


def create_private(client: TestClient, owner: str = "alice") -> str:
    login_as(client, owner)
    response = client.post(
        "/api/documents/text",
        json={"title": "인사 규정", "content": "연차 규정", "visibility": "private"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def grant_rows(dsn: str, document_id: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT user_id, group_id FROM document_grants WHERE document_id = %s ORDER BY 1, 2",
            (document_id,),
        ).fetchall()


def document_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM documents").fetchone()[0]


def test_owner_reads_access(db_client: TestClient):
    doc = create_private(db_client)

    response = db_client.get(f"/api/documents/{doc}/access")

    assert response.status_code == 200
    assert response.json() == {"visibility": "private", "users": [], "groups": [], "follows_folder": True, "folder": None, "folder_scope": None, "hidden_folder": False}


def test_owner_replaces_access_and_grantee_can_read(db_client: TestClient, migrated_db: str):
    make_group(db_client, migrated_db)
    ensure_users(db_client, "bob")
    doc = create_private(db_client)

    response = db_client.put(
        f"/api/documents/{doc}/access",
        json={"visibility": "private", "users": ["bob"], "groups": ["인사팀"]},
    )

    assert response.status_code == 200
    assert response.json() == {"visibility": "private", "users": ["bob"], "groups": ["인사팀"], "follows_folder": True, "folder": None, "folder_scope": None, "hidden_folder": False}
    login_as(db_client, "bob")
    assert db_client.get(f"/api/documents/{doc}").status_code == 200


def test_put_public_with_grantees_is_400_and_changes_nothing(
    db_client: TestClient, migrated_db: str
):
    ensure_users(db_client, "bob")
    doc = create_private(db_client)

    response = db_client.put(
        f"/api/documents/{doc}/access", json={"visibility": "public", "users": ["bob"]}
    )

    assert response.status_code == 400
    assert "visibility=private" in response.json()["detail"]
    assert db_client.get(f"/api/documents/{doc}/access").json()["visibility"] == "private"


def test_put_unknown_name_is_400_with_name(db_client: TestClient, migrated_db: str):
    doc = create_private(db_client)

    response = db_client.put(
        f"/api/documents/{doc}/access", json={"visibility": "private", "users": ["ghost"]}
    )

    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]
    assert grant_rows(migrated_db, doc) == []


def test_put_invalid_visibility_is_422(db_client: TestClient):
    doc = create_private(db_client)

    response = db_client.put(f"/api/documents/{doc}/access", json={"visibility": "secret"})

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["get", "put"])
def test_visible_non_owner_gets_403(db_client: TestClient, method: str):
    login_as(db_client, "alice")
    doc = db_client.post(
        "/api/documents/text", json={"title": "공개 문서", "content": "본문"}
    ).json()["id"]
    login_as(db_client, "bob")

    response = db_client.request(
        method.upper(), f"/api/documents/{doc}/access", json={"visibility": "private"}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("target", ["hidden", "missing"])
def test_invisible_or_missing_document_gets_404(db_client: TestClient, method: str, target: str):
    doc = create_private(db_client) if target == "hidden" else MISSING_ID
    login_as(db_client, "bob")

    response = db_client.request(
        method.upper(), f"/api/documents/{doc}/access", json={"visibility": "private"}
    )

    assert response.status_code == 404


@pytest.mark.parametrize("method", ["get", "put"])
def test_anonymous_gets_401(db_client: TestClient, method: str):
    doc = create_private(db_client)
    db_client.post("/api/auth/logout")

    response = db_client.request(
        method.upper(), f"/api/documents/{doc}/access", json={"visibility": "private"}
    )

    assert response.status_code == 401


def test_changing_access_is_session_only(db_client: TestClient, migrated_db: str):
    ensure_users(db_client, "bob")
    doc = create_private(db_client)
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    db_client.post("/api/auth/logout")

    put = db_client.put(
        f"/api/documents/{doc}/access",
        headers=bearer(token),
        json={"visibility": "private", "users": ["bob"]},
    )
    get = db_client.get(f"/api/documents/{doc}/access", headers=bearer(token))

    assert put.status_code == 403
    assert grant_rows(migrated_db, doc) == []
    assert get.status_code == 200
    assert get.json() == {"visibility": "private", "users": [], "groups": [], "follows_folder": True, "folder": None, "folder_scope": None, "hidden_folder": False}


def test_upload_with_grantees_creates_grants(db_client: TestClient, migrated_db: str):
    make_group(db_client, migrated_db)
    ensure_users(db_client, "bob", "carol")

    response = upload_document(
        db_client,
        data={
            "visibility": "private",
            "grant_users": ["bob", "carol"],
            "grant_groups": "인사팀",
        },
    )

    assert response.status_code == 201
    doc = response.json()["id"]
    access = db_client.get(f"/api/documents/{doc}/access").json()
    assert access == {"visibility": "private", "users": ["bob", "carol"], "groups": ["인사팀"], "follows_folder": True, "folder": None, "folder_scope": None, "hidden_folder": False}


def test_upload_with_grantees_works_with_read_write_token(
    db_client: TestClient, migrated_db: str
):
    ensure_users(db_client, "bob")
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    db_client.post("/api/auth/logout")

    response = db_client.post(
        "/api/documents",
        headers=bearer(token),
        files={"file": ("guide.txt", b"OpenSQL guide", "application/octet-stream")},
        data={"visibility": "private", "grant_users": "bob"},
    )

    assert response.status_code == 201
    assert len(grant_rows(migrated_db, response.json()["id"])) == 1


def test_text_api_with_grantees_creates_grants(db_client: TestClient, migrated_db: str):
    make_group(db_client, migrated_db)
    ensure_users(db_client, "bob")
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    db_client.post("/api/auth/logout")

    response = db_client.post(
        "/api/documents/text",
        headers=bearer(token),
        json={
            "title": "인사 규정",
            "content": "연차",
            "visibility": "private",
            "grant_users": ["bob"],
            "grant_groups": ["인사팀"],
        },
    )

    assert response.status_code == 201
    login_as(db_client, "alice")
    access = db_client.get(f"/api/documents/{response.json()['id']}/access").json()
    assert access == {"visibility": "private", "users": ["bob"], "groups": ["인사팀"], "follows_folder": True, "folder": None, "folder_scope": None, "hidden_folder": False}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"visibility": "public", "grant_users": ["bob"]}, "visibility=private"),
        ({"visibility": "private", "grant_users": ["ghost"]}, "ghost"),
        ({"visibility": "private", "grant_groups": ["없는팀"]}, "없는팀"),
        ({"visibility": "private", "grant_users": ["alice"]}, "소유자"),
    ],
)
def test_text_api_invalid_grantees_is_400_and_creates_nothing(
    db_client: TestClient, migrated_db: str, body: dict, expected: str
):
    ensure_users(db_client, "bob")
    login_as(db_client, "alice")
    before = document_count(migrated_db)

    response = db_client.post(
        "/api/documents/text", json={"title": "규정", "content": "본문", **body}
    )

    assert response.status_code == 400
    assert expected in response.json()["detail"]
    assert document_count(migrated_db) == before


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"visibility": "public", "grant_users": "bob"}, "visibility=private"),
        ({"visibility": "private", "grant_users": "ghost"}, "ghost"),
    ],
)
def test_upload_invalid_grantees_is_400_and_creates_nothing(
    db_client: TestClient, migrated_db: str, data: dict, expected: str
):
    ensure_users(db_client, "bob")
    before = document_count(migrated_db)

    response = upload_document(db_client, data=data)

    assert response.status_code == 400
    assert expected in response.json()["detail"]
    assert document_count(migrated_db) == before


def test_same_idempotency_key_with_different_grantees_is_rejected(db_client: TestClient):
    ensure_users(db_client, "bob", "carol")
    login_as(db_client, "alice")
    body = {"title": "규정", "content": "본문", "visibility": "private"}
    headers = {"Idempotency-Key": "access-key"}

    first = db_client.post(
        "/api/documents/text", headers=headers, json={**body, "grant_users": ["bob"]}
    )
    second = db_client.post(
        "/api/documents/text", headers=headers, json={**body, "grant_users": ["carol"]}
    )

    assert first.status_code == 201
    assert second.status_code == 422


def test_summary_does_not_leak_grantees(db_client: TestClient):
    ensure_users(db_client, "bob")
    login_as(db_client, "alice")
    response = db_client.post(
        "/api/documents/text",
        json={"title": "규정", "content": "본문", "visibility": "private", "grant_users": ["bob"]},
    )

    assert response.status_code == 201
    assert "grant_users" not in response.json()
    assert "users" not in response.json()


def test_document_individual_scope_hides_folder_and_can_resume_inheritance(db_client):
    login_as(db_client, "alice")
    root = db_client.post("/api/folders", json={"name": "비밀"}).json()
    assert db_client.put(f"/api/folders/{root['id']}/access", json={"visibility": "private"}).status_code == 200
    login_as(db_client, "bob")
    doc = db_client.post("/api/documents/text", json={"title": "자료", "content": "본문"}).json()
    login_as(db_client, "alice")
    assert db_client.put(f"/api/folders/{root['id']}/access", json={"visibility": "public"}).status_code == 200
    login_as(db_client, "bob")
    assert db_client.put(f"/api/documents/{doc['id']}/folder", json={"folder_id": root["id"]}).status_code == 200
    access = db_client.get(f"/api/documents/{doc['id']}/access").json()
    assert access["follows_folder"] is True and access["folder_scope"] == {"visibility": "public", "users": [], "groups": []}
    changed = db_client.put(f"/api/documents/{doc['id']}/access", json={"visibility": "public", "follows_folder": False})
    assert changed.status_code == 200 and changed.json()["follows_folder"] is False
    login_as(db_client, "alice")
    assert db_client.put(f"/api/folders/{root['id']}/access", json={"visibility": "private"}).status_code == 200
    login_as(db_client, "bob")
    detail = db_client.get(f"/api/documents/{doc['id']}")
    assert detail.status_code == 200 and detail.json()["folder"] is None
    assert detail.json()["hidden_folder"] is True
    access = db_client.get(f"/api/documents/{doc['id']}/access").json()
    assert access["folder"] is None and access["folder_scope"] is None
    assert access["hidden_folder"] is True
    resumed = db_client.put(f"/api/documents/{doc['id']}/access", json={"follows_folder": True})
    assert resumed.status_code == 200 and resumed.json()["follows_folder"] is True
    ignored = db_client.put(f"/api/documents/{doc['id']}/access", json={"visibility": "public"})
    assert ignored.status_code == 400
    assert ignored.json()["detail"].startswith("폴더 범위를 따르는 문서는 개별 지정으로 바꿔야")


def test_unfiled_document_cannot_follow_folder(db_client):
    doc = create_private(db_client)
    response = db_client.put(f"/api/documents/{doc}/access", json={"follows_folder": True})
    assert response.status_code == 400
    assert response.json()["detail"] == "폴더에 없는 문서는 폴더 범위를 따를 수 없습니다."


def test_follow_folder_with_scope_values_is_rejected(db_client):
    login_as(db_client, "bob")
    root = db_client.post("/api/folders", json={"name": "공용"}).json()
    doc = db_client.post("/api/documents/text", json={"title": "자료", "content": "본문"}).json()
    assert db_client.put(f"/api/documents/{doc['id']}/folder", json={"folder_id": root["id"]}).status_code == 200
    assert db_client.put(
        f"/api/documents/{doc['id']}/access", json={"visibility": "private", "follows_folder": False}
    ).status_code == 200
    response = db_client.put(
        f"/api/documents/{doc['id']}/access", json={"visibility": "private", "follows_folder": True}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "폴더 범위를 따르면 공개범위·부여 대상을 함께 지정할 수 없습니다."
    access = db_client.get(f"/api/documents/{doc['id']}/access").json()
    assert access["follows_folder"] is False and access["visibility"] == "private"


@pytest.mark.parametrize("visibility,still_visible,denied", [("public", True, 403), ("private", False, 404)])
def test_transfer_owner_moves_all_write_permissions(db_client, migrated_db, visibility, still_visible, denied):
    ensure_users(db_client, "lee")
    login_as(db_client, "kim")
    doc = db_client.post("/api/documents/text", json={
        "title": "이전", "content": "내용", "visibility": visibility,
    }).json()["id"]
    response = db_client.put(f"/api/documents/{doc}/owner", json={"owner": "lee"})
    assert response.status_code == 200
    assert response.json() == {"owner_id": "lee", "still_visible": still_visible}
    for method, suffix, body in (
        ("PUT", "", {"content": "수정", "version": 1}),
        ("PUT", "/tags", {"tags": ["이전"]}),
        ("PUT", "/access", {"visibility": visibility}),
        ("DELETE", "", None),
    ):
        assert db_client.request(method, f"/api/documents/{doc}{suffix}", json=body).status_code == denied
    login_as(db_client, "lee")
    assert db_client.put(f"/api/documents/{doc}", json={"content": "수정", "version": 1}).status_code == 200
    assert db_client.put(f"/api/documents/{doc}/tags", json={"tags": ["이전"]}).status_code == 200
    assert db_client.put(f"/api/documents/{doc}/access", json={"visibility": visibility}).status_code == 200
    assert db_client.delete(f"/api/documents/{doc}").status_code == 204
    login_admin(db_client, migrated_db)
    entries = db_client.get("/api/admin/audit", params={"action": "owner_changed"}).json()["items"]
    assert len(entries) == 1
    assert entries[0]["actor"] == "kim"
    assert entries[0]["actor_via"] == "session"
    assert entries[0]["detail"] == {"kind": "document", "before": "kim", "after": "lee"}


@pytest.mark.parametrize("target,code", [("ghost", 400), ("alice", 400), ("", 422), (None, 422)])
def test_transfer_owner_invalid_target_leaves_owner(db_client, target, code):
    doc = create_private(db_client)
    response = db_client.put(f"/api/documents/{doc}/owner", json=None if target is None else {"owner": target})
    assert response.status_code == code
    assert db_client.get(f"/api/documents/{doc}").json()["owner_id"] == "alice"


@pytest.mark.parametrize("visibility,code", [("public", 403), ("private", 404)])
def test_transfer_owner_non_owner(db_client, visibility, code):
    doc = create_private(db_client)
    db_client.put(f"/api/documents/{doc}/access", json={"visibility": visibility})
    login_as(db_client, "bob")
    assert db_client.put(f"/api/documents/{doc}/owner", json={"owner": "bob"}).status_code == code


def test_transfer_owner_requires_session(db_client):
    ensure_users(db_client, "lee")
    doc = create_private(db_client)
    token = issue_token(db_client, "alice", scope="read_write")["token"]
    response = db_client.put(f"/api/documents/{doc}/owner", json={"owner": "lee"}, headers=bearer(token))
    assert response.status_code == 403
    assert response.json()["detail"] == "로그인 세션이 필요합니다."
    db_client.cookies.clear()
    assert db_client.put(f"/api/documents/{doc}/owner", json={"owner": "lee"}).status_code == 401
