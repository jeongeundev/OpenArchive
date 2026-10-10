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


def test_share_name_longer_than_a_token_name_is_422(db_client: TestClient, migrated_db: str):
    """공유 이름도 토큰 이름과 같은 100자 상한을 둔다."""
    login_as(db_client, "alice")

    assert db_client.post("/api/shares", json={"name": "가" * 101}).status_code == 422
    assert table_counts(migrated_db)[0] == 0
    assert db_client.post("/api/shares", json={"name": "가" * 100}).status_code == 201


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


# --- 공유 토큰 만료일·마지막 사용 (ADR-061 결정 1, #199) ---


def test_share_token_expiry_rules(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    path = f"/api/shares/{share['id']}/tokens"

    plain = db_client.post(path, json={"name": "기본"}).json()
    assert (plain["expires_at"], plain["last_used_at"], plain["expired"]) == (None, None, False)

    future = db_client.post(path, json={"name": "미래", "expires_at": "2099-01-01T00:00:00+09:00"})
    assert future.status_code == 201
    assert future.json()["expires_at"] is not None

    past = db_client.post(path, json={"name": "과거", "expires_at": "2000-01-01T00:00:00+09:00"})
    assert past.status_code == 400
    assert past.json()["detail"] == "만료일은 지금 이후여야 합니다."
    naive = db_client.post(path, json={"name": "시간대 없음", "expires_at": "2099-01-01T00:00:00"})
    assert naive.status_code == 422
    assert len(db_client.get("/api/shares").json()[0]["tokens"]) == 2


def test_expired_share_token_is_401_and_listed_as_expired(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    issued = db_client.post(f"/api/shares/{share['id']}/tokens", json={"name": "연동"}).json()
    db_client.cookies.clear()

    used = db_client.get("/api/documents", headers={"Authorization": f"Bearer {issued['token']}"})
    assert used.status_code == 200
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE api_tokens SET expires_at = now() - interval '1 minute' WHERE id = %s",
            (issued["id"],),
        )
    expired = db_client.get("/api/documents", headers={"Authorization": f"Bearer {issued['token']}"})
    assert expired.status_code == 401

    login_as(db_client, "alice")
    [token] = db_client.get("/api/shares").json()[0]["tokens"]
    assert token["expired"] is True
    assert token["last_used_at"] is not None
    assert "token" not in token and "token_hash" not in token


# --- 공유에 폴더 넣기·빼기 (#206) ---


def create_folder(client: TestClient, name: str, parent_id: str | None = None) -> str:
    response = client.post("/api/folders", json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201
    return response.json()["id"]


def share_folder_rows(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM share_folders").fetchone()[0]


def test_add_and_remove_folder_is_idempotent(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    root = create_folder(db_client, "제품")
    child = create_folder(db_client, "설명서", root)
    path = f"/api/shares/{share['id']}/folders/{child}"

    assert db_client.put(path).status_code == 204
    assert db_client.put(path).status_code == 204
    listed = db_client.get("/api/shares").json()
    assert listed[0]["folders"] == [{"id": child, "name": "설명서"}]
    assert share_folder_rows(migrated_db) == 1

    assert db_client.delete(path).status_code == 204
    assert db_client.delete(path).status_code == 204
    assert db_client.get("/api/shares").json()[0]["folders"] == []
    assert share_folder_rows(migrated_db) == 0


@pytest.mark.parametrize("method", ["put", "delete"])
def test_folder_on_someone_elses_or_missing_share_is_404(
    db_client: TestClient, migrated_db: str, method: str
):
    login_as(db_client, "bob")
    bobs = create_share(db_client, "bob 공유")
    login_as(db_client, "alice")
    root = create_folder(db_client, "제품")

    for share_id in (bobs["id"], MISSING_ID):
        response = getattr(db_client, method)(f"/api/shares/{share_id}/folders/{root}")
        assert response.status_code == 404
        assert response.json()["detail"] == "공유를 찾을 수 없습니다."
    assert share_folder_rows(migrated_db) == 0


@pytest.mark.parametrize("method", ["put", "delete"])
def test_invisible_folder_is_404_and_non_creator_is_403(
    db_client: TestClient, migrated_db: str, method: str
):
    login_as(db_client, "bob")
    bob_public = create_folder(db_client, "bob 공개")
    bob_private = create_folder(db_client, "bob 비공개")
    assert db_client.put(
        f"/api/folders/{bob_private}/access",
        json={"visibility": "private", "users": [], "groups": []},
    ).status_code == 200
    login_as(db_client, "alice")
    share = create_share(db_client)
    # 남의 최상위 폴더 아래에 alice가 만든 하위 폴더도 넣지 못한다 — 범위는 최상위 만든 사람이 정한다.
    alices_child = create_folder(db_client, "alice 하위", bob_public)

    for folder_id in (bob_private, MISSING_ID):
        response = getattr(db_client, method)(f"/api/shares/{share['id']}/folders/{folder_id}")
        assert response.status_code == 404
        assert response.json()["detail"] == "폴더를 찾을 수 없습니다."
    for folder_id in (bob_public, alices_child):
        response = getattr(db_client, method)(f"/api/shares/{share['id']}/folders/{folder_id}")
        assert response.status_code == 403
        assert response.json()["detail"] == "폴더를 관리할 권한이 없습니다."
    assert share_folder_rows(migrated_db) == 0


def test_folder_share_endpoints_are_session_only(db_client: TestClient, migrated_db: str):
    login_as(db_client, "alice")
    share = create_share(db_client)
    root = create_folder(db_client, "제품")
    share_token = db_client.post(
        f"/api/shares/{share['id']}/tokens", json={"name": "연동"}
    ).json()["token"]
    user_token = issue_user_token(migrated_db)
    db_client.cookies.clear()
    path = f"/api/shares/{share['id']}/folders/{root}"

    for token in (user_token, share_token):
        for method in ("put", "delete"):
            response = db_client.request(method, path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 403, (method, response.text)
    assert db_client.put(path).status_code == 401
    assert share_folder_rows(migrated_db) == 0
