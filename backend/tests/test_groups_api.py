"""관리자 그룹 API와 부여 대상 목록 API (ADR-044 관리 경로)."""

import asyncio
import hashlib
from uuid import UUID

import psycopg
import pytest
from conftest import insert_test_document, login_as
from fastapi.testclient import TestClient

from openarchive.services import grants
from openarchive.services.auth import hash_password

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def login_admin(client: TestClient, dsn: str, username: str = "boss") -> None:
    login_as(client, username)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE users SET is_admin = true WHERE username = %s", (username,))


def ensure_user(dsn: str, username: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
            "ON CONFLICT (username) DO NOTHING",
            (username, hash_password("test-password")),
        )


def issue_admin_token(dsn: str, username: str = "boss") -> str:
    token = f"{username}-admin-read-write-token"
    with psycopg.connect(dsn) as conn:
        user_id = conn.execute(
            "UPDATE users SET is_admin = true WHERE username = %s RETURNING id", (username,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) "
            "VALUES (%s, 'admin-test', %s, 'read_write')",
            (user_id, hashlib.sha256(token.encode()).hexdigest()),
        )
    return token


def create_group(client: TestClient, name: str = "인사팀") -> dict:
    response = client.post("/api/admin/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_admin_creates_a_group(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)

    response = db_client.post("/api/admin/groups", json={"name": "인사팀"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "인사팀"
    assert body["members"] == []
    UUID(body["id"])
    assert body["created_at"]


def test_duplicate_group_name_is_409(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    create_group(db_client)

    response = db_client.post("/api/admin/groups", json={"name": "인사팀"})

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 존재하는 그룹 이름입니다."


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_group_name_is_422(db_client: TestClient, migrated_db: str, name: str):
    login_admin(db_client, migrated_db)

    assert db_client.post("/api/admin/groups", json={"name": name}).status_code == 422


def test_list_groups_includes_member_usernames(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    group = create_group(db_client)
    create_group(db_client, "재무팀")
    db_client.put(f"/api/admin/groups/{group['id']}/members/carol")

    response = db_client.get("/api/admin/groups")

    assert response.status_code == 200
    by_name = {item["name"]: item for item in response.json()}
    assert by_name["인사팀"]["members"] == ["carol"]
    assert by_name["재무팀"]["members"] == []


def test_adding_a_member_is_idempotent(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    group = create_group(db_client)

    first = db_client.put(f"/api/admin/groups/{group['id']}/members/carol")
    second = db_client.put(f"/api/admin/groups/{group['id']}/members/carol")

    assert first.status_code == 204
    assert second.status_code == 204
    assert db_client.get("/api/admin/groups").json()[0]["members"] == ["carol"]


@pytest.mark.parametrize("method", ["put", "delete"])
def test_member_change_on_missing_group_or_user_is_404(
    db_client: TestClient, migrated_db: str, method: str
):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    group = create_group(db_client)
    send = getattr(db_client, method)

    missing_group = send(f"/api/admin/groups/{MISSING_ID}/members/carol")
    missing_user = send(f"/api/admin/groups/{group['id']}/members/nobody")

    assert missing_group.status_code == 404
    assert missing_group.json()["detail"] == "그룹을 찾을 수 없습니다."
    assert missing_user.status_code == 404
    assert missing_user.json()["detail"] == "사용자를 찾을 수 없습니다."


def test_removing_a_member_is_idempotent(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    group = create_group(db_client)
    db_client.put(f"/api/admin/groups/{group['id']}/members/carol")

    removed = db_client.delete(f"/api/admin/groups/{group['id']}/members/carol")
    again = db_client.delete(f"/api/admin/groups/{group['id']}/members/carol")

    assert removed.status_code == 204
    assert again.status_code == 204
    assert db_client.get("/api/admin/groups").json()[0]["members"] == []


def test_admin_deletes_a_group(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    group = create_group(db_client)

    deleted = db_client.delete(f"/api/admin/groups/{group['id']}")
    missing = db_client.delete(f"/api/admin/groups/{group['id']}")

    assert deleted.status_code == 204
    assert db_client.get("/api/admin/groups").json() == []
    assert missing.status_code == 404
    assert missing.json()["detail"] == "그룹을 찾을 수 없습니다."


ENDPOINTS = [
    ("post", "/api/admin/groups", {"name": "경계팀"}),
    ("get", "/api/admin/groups", None),
    ("delete", f"/api/admin/groups/{MISSING_ID}", None),
    ("put", f"/api/admin/groups/{MISSING_ID}/members/carol", None),
    ("delete", f"/api/admin/groups/{MISSING_ID}/members/carol", None),
]


def call(client: TestClient, method: str, path: str, body, headers=None):
    kwargs = {"headers": headers} if headers else {}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_group_endpoints_reject_non_admin_sessions(
    db_client: TestClient, migrated_db: str, method: str, path: str, body
):
    login_as(db_client, "alice")

    assert call(db_client, method, path, body).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_group_endpoints_reject_anonymous(
    db_client: TestClient, migrated_db: str, method: str, path: str, body
):
    assert call(db_client, method, path, body).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_group_endpoints_reject_an_admins_api_token(
    db_client: TestClient, migrated_db: str, method: str, path: str, body
):
    login_as(db_client, "boss")
    token = issue_admin_token(migrated_db)
    db_client.cookies.clear()

    response = call(
        db_client, method, path, body, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


def test_principals_lists_user_and_group_names_for_a_session(
    db_client: TestClient, migrated_db: str
):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    create_group(db_client)
    login_as(db_client, "alice")

    response = db_client.get("/api/principals")

    assert response.status_code == 200
    # 요청자 자신은 부여 대상이 될 수 없어 빠진다
    assert response.json() == {"users": ["boss", "carol"], "groups": ["인사팀"]}


def test_principals_accepts_an_api_token(db_client: TestClient, migrated_db: str):
    login_as(db_client, "boss")
    token = issue_admin_token(migrated_db)
    db_client.cookies.clear()

    response = db_client.get(
        "/api/principals", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"users": [], "groups": []}


def test_principals_rejects_anonymous(db_client: TestClient, migrated_db: str):
    assert db_client.get("/api/principals").status_code == 401


def test_group_membership_changes_document_visibility(
    db_client: TestClient, migrated_db: str
):
    login_admin(db_client, migrated_db)
    ensure_user(migrated_db, "carol")
    group = create_group(db_client)

    async def seed() -> UUID:
        async with await psycopg.AsyncConnection.connect(migrated_db) as conn:
            document_id = await insert_test_document(
                conn, title="인사 규정", content="제한 문서", owner_id="boss",
                visibility="private",
            )
            await grants.insert_grants(conn, document_id, [], [UUID(group["id"])])
            await conn.commit()
            return document_id

    document_id = asyncio.run(seed())
    path = f"/api/documents/{document_id}"

    login_as(db_client, "carol")
    assert db_client.get(path).status_code == 404

    login_admin(db_client, migrated_db)
    assert db_client.put(f"/api/admin/groups/{group['id']}/members/carol").status_code == 204
    db_client.cookies.clear()
    login_as(db_client, "carol")
    assert db_client.get(path).status_code == 200

    login_admin(db_client, migrated_db)
    assert (
        db_client.delete(f"/api/admin/groups/{group['id']}/members/carol").status_code == 204
    )
    db_client.cookies.clear()
    login_as(db_client, "carol")
    assert db_client.get(path).status_code == 404
