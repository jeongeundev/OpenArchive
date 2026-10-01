"""공유 관리 API — 세션 전용 (ADR-044 「공유」, ADR-034 결정 6)."""

import hashlib
from uuid import UUID

import psycopg
import pytest
from conftest import login_as
from fastapi.testclient import TestClient

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def insert_document(dsn: str, *, title: str, owner: str = "alice", visibility: str = "public") -> str:
    content = f"{title} 본문"
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO documents (title, content_type, content, content_hash, owner_id, visibility)
            VALUES (%s, 'md', %s, %s, %s, %s) RETURNING id
            """,
            (title, content, hashlib.sha256(content.encode()).hexdigest(), owner, visibility),
        ).fetchone()
    return str(row[0])


def issue_user_token(dsn: str, username: str = "alice") -> str:
    token = f"{username}-read-write-token"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO api_tokens (user_id, name, token_hash, scope)
            SELECT id, 'test', %s, 'read_write' FROM users WHERE username = %s
            """,
            (hashlib.sha256(token.encode()).hexdigest(), username),
        )
    return token


def create_share(client: TestClient, name: str = "B사") -> dict:
    response = client.post("/api/shares", json={"name": name})
    assert response.status_code == 201
    return response.json()


def table_counts(dsn: str) -> tuple[int, int, int]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            """
            SELECT (SELECT count(*) FROM shares),
                   (SELECT count(*) FROM document_grants WHERE share_id IS NOT NULL),
                   (SELECT count(*) FROM api_tokens WHERE share_id IS NOT NULL)
            """
        ).fetchone()


def test_create_share(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")

    response = db_client.post("/api/shares", json={"name": "B사"})

    assert response.status_code == 201
    body = response.json()
    UUID(body["id"])
    assert body["name"] == "B사"
    assert body["created_at"]
    assert body["documents"] == []
    assert body["tokens"] == []


def test_duplicate_share_name_is_409(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    create_share(db_client)

    response = db_client.post("/api/shares", json={"name": "B사"})

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 존재하는 공유 이름입니다."


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_share_name_is_422(db_client: TestClient, migrated_db: str, name: str):
    login_as(db_client, "alice")

    assert db_client.post("/api/shares", json={"name": name}).status_code == 422
    assert table_counts(migrated_db)[0] == 0


def test_list_shows_only_own_shares(db_client: TestClient, migrated_db: str):
    login_as(db_client, "bob")
    create_share(db_client, "bob의 공유")
    login_as(db_client, "alice")
    create_share(db_client, "B사")

    response = db_client.get("/api/shares")

    assert response.status_code == 200
    assert [share["name"] for share in response.json()] == ["B사"]


def test_add_and_remove_document_is_idempotent(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    document_id = insert_document(migrated_db, title="제품 문서", visibility="private")
    path = f"/api/shares/{share['id']}/documents/{document_id}"

    assert db_client.put(path).status_code == 204
    assert db_client.put(path).status_code == 204
    [listed] = db_client.get("/api/shares").json()
    assert listed["documents"] == [{"id": document_id, "title": "제품 문서"}]

    assert db_client.delete(path).status_code == 204
    assert db_client.delete(path).status_code == 204
    [listed] = db_client.get("/api/shares").json()
    assert listed["documents"] == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_someone_elses_or_missing_share_is_404(
    db_client: TestClient, migrated_db: str, method: str
):
    login_as(db_client, "bob")
    bobs_share = create_share(db_client, "bob의 공유")
    login_as(db_client, "alice")
    document_id = insert_document(migrated_db, title="제품 문서")

    for share_id in (bobs_share["id"], MISSING_ID):
        response = getattr(db_client, method)(
            f"/api/shares/{share_id}/documents/{document_id}"
        )
        assert response.status_code == 404
    assert table_counts(migrated_db)[1] == 0


@pytest.mark.parametrize("method", ["put", "delete"])
def test_invisible_document_is_404_and_visible_foreign_document_is_403(
    db_client: TestClient, migrated_db: str, method: str
):
    login_as(db_client, "alice")
    share = create_share(db_client)
    hidden = insert_document(migrated_db, title="bob 비공개", owner="bob", visibility="private")
    visible = insert_document(migrated_db, title="bob 공개", owner="bob", visibility="public")
    call = getattr(db_client, method)

    assert call(f"/api/shares/{share['id']}/documents/{hidden}").status_code == 404
    assert call(f"/api/shares/{share['id']}/documents/{MISSING_ID}").status_code == 404
    assert call(f"/api/shares/{share['id']}/documents/{visible}").status_code == 403
    assert table_counts(migrated_db)[1] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/shares/not-a-uuid",
        "/api/shares/not-a-uuid/documents/" + MISSING_ID,
        "/api/shares/" + MISSING_ID + "/documents/not-a-uuid",
    ],
)
def test_malformed_uuid_is_422(db_client: TestClient, migrated_db: str, path: str):
    login_as(db_client, "alice")

    assert db_client.delete(path).status_code == 422


def test_share_token_issue_list_and_revoke(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)

    issued = db_client.post(f"/api/shares/{share['id']}/tokens", json={"name": "연동"})

    assert issued.status_code == 201
    body = issued.json()
    assert body["scope"] == "read"
    assert body["name"] == "연동"
    assert body["token"]
    [listed] = db_client.get("/api/shares").json()
    assert [token["id"] for token in listed["tokens"]] == [body["id"]]
    assert "token" not in listed["tokens"][0]
    assert body["token"] not in db_client.get("/api/shares").text

    revoke = f"/api/shares/{share['id']}/tokens/{body['id']}"
    assert db_client.delete(revoke).status_code == 204
    assert db_client.delete(revoke).status_code == 404
    assert db_client.get("/api/shares").json()[0]["tokens"] == []


def test_share_token_on_someone_elses_share_is_404(db_client: TestClient, migrated_db: str):
    login_as(db_client, "bob")
    bobs_share = create_share(db_client, "bob의 공유")
    login_as(db_client, "alice")

    response = db_client.post(f"/api/shares/{bobs_share['id']}/tokens", json={"name": "x"})

    assert response.status_code == 404
    assert table_counts(migrated_db)[2] == 0


def test_share_token_is_not_listed_as_a_user_token(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    db_client.post(f"/api/shares/{share['id']}/tokens", json={"name": "연동"})

    response = db_client.get("/api/auth/tokens")

    assert response.status_code == 200
    assert response.json() == []


def test_delete_share(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)

    assert db_client.delete(f"/api/shares/{share['id']}").status_code == 204
    assert db_client.delete(f"/api/shares/{share['id']}").status_code == 404
    assert db_client.get("/api/shares").json() == []


def endpoints(share_id: str, document_id: str, token_id: str) -> list[tuple[str, str, dict | None]]:
    return [
        ("post", "/api/shares", {"name": "새 공유"}),
        ("get", "/api/shares", None),
        ("delete", f"/api/shares/{share_id}", None),
        ("put", f"/api/shares/{share_id}/documents/{document_id}", None),
        ("delete", f"/api/shares/{share_id}/documents/{document_id}", None),
        ("post", f"/api/shares/{share_id}/tokens", {"name": "새 토큰"}),
        ("delete", f"/api/shares/{share_id}/tokens/{token_id}", None),
    ]


def call(client: TestClient, method: str, path: str, body, headers=None):
    if body is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, json=body, headers=headers)


def prepare(db_client: TestClient, migrated_db: str) -> tuple[str, str, str]:
    """공유 하나·그 안의 문서 하나·공유 토큰 하나를 세션으로 만든다."""
    login_as(db_client, "alice")
    share = create_share(db_client)
    document_id = insert_document(migrated_db, title="제품 문서")
    assert db_client.put(f"/api/shares/{share['id']}/documents/{document_id}").status_code == 204
    token = db_client.post(f"/api/shares/{share['id']}/tokens", json={"name": "연동"}).json()
    return share["id"], document_id, token["id"]


def test_share_endpoints_reject_a_users_api_token(db_client: TestClient, migrated_db: str):
    share_id, document_id, token_id = prepare(db_client, migrated_db)
    token = issue_user_token(migrated_db)
    db_client.cookies.clear()
    before = table_counts(migrated_db)

    for method, path, body in endpoints(share_id, document_id, token_id):
        response = call(
            db_client, method, path, body, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403, (method, path)

    assert table_counts(migrated_db) == before


def test_share_endpoints_reject_anonymous(db_client: TestClient, migrated_db: str):
    share_id, document_id, token_id = prepare(db_client, migrated_db)
    db_client.post("/api/auth/logout")
    db_client.cookies.clear()
    before = table_counts(migrated_db)

    for method, path, body in endpoints(share_id, document_id, token_id):
        assert call(db_client, method, path, body).status_code == 401, (method, path)

    assert table_counts(migrated_db) == before
