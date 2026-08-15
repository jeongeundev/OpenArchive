"""API 토큰의 발급부터 공급·검색·열람·폐기까지 전 구간 계약."""

import psycopg
from conftest import login_as, run_embedding_worker
from fastapi.testclient import TestClient


def issue_token(client: TestClient, username: str, *, scope: str = "read") -> dict:
    login_as(client, username)
    response = client.post(
        "/api/auth/tokens",
        json={"name": f"{username}-{scope}", "scope": scope},
    )
    assert response.status_code == 201
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_read_write_token_completes_ingest_search_and_detail_without_a_cookie(
    db_client: TestClient, migrated_db: str
):
    issued = issue_token(db_client, "alice", scope="read_write")
    db_client.cookies.clear()
    headers = bearer(issued["token"])

    created = db_client.post(
        "/api/documents/text",
        headers=headers,
        json={"title": "Token pipeline", "content": "토큰 공급 검색 근거"},
    )
    assert created.status_code == 201
    assert created.json()["owner_id"] == "alice"

    assert run_embedding_worker(migrated_db) == 1
    detail = db_client.get(f"/api/documents/{created.json()['id']}", headers=headers)
    search = db_client.post(
        "/api/search", headers=headers, json={"query": "토큰 공급 검색 근거"}
    )

    assert detail.status_code == 200
    assert detail.json()["embedding_status"] == "ready"
    assert created.json()["id"] in {item["document_id"] for item in search.json()["items"]}


def test_read_token_can_read_but_all_document_writes_are_forbidden(
    db_client: TestClient, migrated_db: str
):
    writer = issue_token(db_client, "alice", scope="read_write")
    db_client.cookies.clear()
    created = db_client.post(
        "/api/documents/text",
        headers=bearer(writer["token"]),
        json={"title": "Read boundary", "content": "읽기 토큰 경계"},
    ).json()
    run_embedding_worker(migrated_db)
    read = issue_token(db_client, "alice", scope="read")
    db_client.cookies.clear()
    headers = bearer(read["token"])

    writes = [
        db_client.post(
            "/api/documents",
            headers=headers,
            files={"file": ("blocked.txt", b"blocked", "text/plain")},
        ),
        db_client.post(
            "/api/documents/text",
            headers=headers,
            json={"title": "Blocked", "content": "blocked"},
        ),
        db_client.put(
            f"/api/documents/{created['id']}",
            headers=headers,
            json={"content": "blocked", "version": 1},
        ),
        db_client.put(
            f"/api/documents/{created['id']}/tags",
            headers=headers,
            json={"tags": ["blocked"]},
        ),
        db_client.delete(f"/api/documents/{created['id']}", headers=headers),
        db_client.post(f"/api/documents/{created['id']}/reembed", headers=headers),
    ]

    assert [response.status_code for response in writes] == [403] * 6
    assert db_client.get("/api/documents", headers=headers).status_code == 200
    assert db_client.get(f"/api/documents/{created['id']}", headers=headers).status_code == 200
    assert db_client.post(
        "/api/search", headers=headers, json={"query": "읽기 토큰 경계"}
    ).status_code == 200


def test_revoking_one_token_preserves_another_token_and_the_login_session(
    db_client: TestClient,
):
    token_a = issue_token(db_client, "alice")
    token_b = db_client.post(
        "/api/auth/tokens", json={"name": "alice-second", "scope": "read"}
    ).json()

    assert db_client.delete(f"/api/auth/tokens/{token_a['id']}").status_code == 204
    assert db_client.get("/api/auth/me", headers=bearer(token_a["token"])).json()[
        "authenticated"
    ] is False
    assert db_client.get("/api/auth/me", headers=bearer(token_b["token"])).json()[
        "username"
    ] == "alice"
    assert db_client.get("/api/auth/me").json()["username"] == "alice"


def test_token_visibility_matches_owner_other_user_and_anonymous_boundaries(
    db_client: TestClient, migrated_db: str
):
    alice = issue_token(db_client, "alice", scope="read_write")
    bob = issue_token(db_client, "bob", scope="read")
    db_client.cookies.clear()
    private = db_client.post(
        "/api/documents/text",
        headers=bearer(alice["token"]),
        json={"title": "Alice secret", "content": "비공개 토큰 문서", "visibility": "private"},
    ).json()
    public = db_client.post(
        "/api/documents/text",
        headers=bearer(alice["token"]),
        json={"title": "Shared token", "content": "공개 토큰 문서", "visibility": "public"},
    ).json()
    run_embedding_worker(migrated_db)

    alice_headers = bearer(alice["token"])
    bob_headers = bearer(bob["token"])
    assert db_client.get(f"/api/documents/{private['id']}", headers=alice_headers).status_code == 200
    assert db_client.get(f"/api/documents/{private['id']}", headers=bob_headers).status_code == 404
    assert db_client.get(f"/api/documents/{private['id']}").status_code == 401
    assert db_client.get(f"/api/documents/{public['id']}", headers=bob_headers).status_code == 200

    bob_list = {item["id"] for item in db_client.get("/api/documents", headers=bob_headers).json()}
    bob_search = db_client.post(
        "/api/search", headers=bob_headers, json={"query": "토큰 문서"}
    ).json()["items"]
    bob_search_ids = {item["document_id"] for item in bob_search}
    assert public["id"] in bob_list
    assert private["id"] not in bob_list
    assert public["id"] in bob_search_ids
    assert private["id"] not in bob_search_ids


def test_unverified_identifiers_never_authenticate_and_valid_token_is_issuer_only(
    db_client: TestClient, migrated_db: str
):
    valid = issue_token(db_client, "bob", scope="read_write")
    revoked = db_client.post(
        "/api/auth/tokens", json={"name": "revoked", "scope": "read"}
    ).json()
    assert db_client.delete(f"/api/auth/tokens/{revoked['id']}").status_code == 204
    with psycopg.connect(migrated_db) as conn:
        stored_hash = conn.execute(
            "SELECT token_hash FROM api_tokens WHERE id = %s", (valid["id"],)
        ).fetchone()[0]
    db_client.cookies.clear()

    changed = valid["token"][:-1] + ("A" if valid["token"][-1] != "A" else "B")
    for unverified in ("alice", "arbitrary", revoked["token"], changed, stored_hash):
        assert db_client.get("/api/documents", headers=bearer(unverified)).status_code == 401

    response = db_client.post(
        "/api/documents/text",
        headers=bearer(valid["token"]),
        json={
            "title": "Issuer only",
            "content": "주체 사칭 방지",
            "owner_id": "alice",
            "username": "alice",
        },
    )
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        assert response.json()["owner_id"] == "bob"
        assert response.json()["id"] in {
            item["id"]
            for item in db_client.get(
                "/api/documents", headers=bearer(valid["token"])
            ).json()
        }
    assert db_client.get("/api/auth/me", headers=bearer(valid["token"])).json()[
        "username"
    ] == "bob"


def test_admin_read_write_token_cannot_cross_session_only_management_boundaries(
    db_client: TestClient, migrated_db: str
):
    login_as(db_client, "admin")
    with psycopg.connect(migrated_db) as conn:
        conn.execute("UPDATE users SET is_admin = true WHERE username = 'admin'")
    login_as(db_client, "admin")
    token = db_client.post(
        "/api/auth/tokens", json={"name": "admin-rw", "scope": "read_write"}
    ).json()
    session_token = db_client.post(
        "/api/auth/tokens", json={"name": "session-control", "scope": "read"}
    ).json()
    created_user = db_client.post(
        "/api/admin/users",
        json={"username": "managed-user", "password": "secret"},
    ).json()

    headers = bearer(token["token"])
    token_responses = [
        db_client.post("/api/auth/tokens", headers=headers, json={"name": "nested"}),
        db_client.get("/api/auth/tokens", headers=headers),
        db_client.delete(f"/api/auth/tokens/{session_token['id']}", headers=headers),
        db_client.post(
            "/api/admin/users",
            headers=headers,
            json={"username": "blocked-user", "password": "secret"},
        ),
        db_client.get("/api/admin/users", headers=headers),
        db_client.delete(f"/api/admin/users/{created_user['id']}", headers=headers),
    ]
    assert [response.status_code for response in token_responses] == [403] * 6

    assert db_client.post("/api/auth/tokens", json={"name": "session-ok"}).status_code == 201
    assert db_client.get("/api/auth/tokens").status_code == 200
    assert db_client.delete(f"/api/auth/tokens/{session_token['id']}").status_code == 204
    second_user = db_client.post(
        "/api/admin/users", json={"username": "second-user", "password": "secret"}
    )
    assert second_user.status_code == 201
    assert db_client.get("/api/admin/users").status_code == 200
    assert db_client.delete(f"/api/admin/users/{second_user.json()['id']}").status_code == 204
