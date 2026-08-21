import hashlib
import json
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.api.deps import SESSION_COOKIE
from app.services.auth import hash_password


def create_user(dsn: str, username: str = "alice", password: str = "secret") -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            (username, hash_password(password)),
        )


def issue_token(dsn: str, username: str, scope: str = "read") -> str:
    token = f"{username}-api-token"
    with psycopg.connect(dsn) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = %s", (username,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'test', %s, %s)",
            (user_id, hashlib.sha256(token.encode()).hexdigest(), scope),
        )
    return token


def test_login_sets_httponly_cookie_and_returns_only_public_user_fields(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)

    response = db_client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret"}
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "username": "alice", "is_admin": False}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "password_hash" not in response.text
    assert "token" not in response.text


def test_me_uses_the_login_cookie_and_ignores_x_user_id(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)

    anonymous = db_client.get("/api/auth/me", headers={"X-User-Id": "alice"})
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    authenticated = db_client.get("/api/auth/me")

    assert anonymous.json() == {"authenticated": False, "username": None, "is_admin": False}
    assert authenticated.json() == {
        "authenticated": True,
        "username": "alice",
        "is_admin": False,
    }


def test_logout_invalidates_the_cookie_backed_session(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    issued_token = db_client.cookies[SESSION_COOKIE]

    response = db_client.post("/api/auth/logout")
    db_client.cookies.set(SESSION_COOKIE, issued_token)

    assert response.status_code == 200
    assert db_client.get("/api/auth/me").json() == {
        "authenticated": False,
        "username": None,
        "is_admin": False,
    }


def test_invalid_credentials_do_not_set_a_cookie(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)

    response = db_client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong"}
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_me_accepts_a_valid_bearer_token_without_a_cookie(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    token = issue_token(migrated_db, "alice")

    response = db_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.json() == {
        "authenticated": True,
        "username": "alice",
        "is_admin": False,
    }


def test_bearer_token_takes_precedence_over_another_users_cookie(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db, "alice")
    create_user(migrated_db, "bob")
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    token = issue_token(migrated_db, "bob")

    response = db_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.json()["username"] == "bob"


def test_invalid_bearer_does_not_fall_back_to_a_valid_cookie(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    response = db_client.get(
        "/api/auth/me", headers={"Authorization": "Bearer revoked-token"}
    )

    assert response.json() == {
        "authenticated": False,
        "username": None,
        "is_admin": False,
    }


def test_empty_bearer_value_is_anonymous_and_does_not_reach_the_cookie(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    response = db_client.get("/api/auth/me", headers={"Authorization": "Bearer "})

    assert response.json()["authenticated"] is False


def test_another_authorization_scheme_leaves_the_cookie_session_intact(
    db_client: TestClient, migrated_db: str
):
    """Basic 등 다른 스킴은 이 앱을 향한 토큰이 아니므로 세션을 무력화하지 않는다.

    Bearer 폴백 금지의 근거(폐기가 관측되지 않는다·read 토큰이 조용히 승격된다)는
    토큰이 실제로 제시됐을 때만 성립한다. 여기에는 폐기할 토큰도 좁힌 scope도 없다.
    Authorization을 덧붙이는 프록시 뒤에서 브라우저 세션이 조용히 끊기면 안 된다.
    """
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    response = db_client.get("/api/auth/me", headers={"Authorization": "Basic abc"})

    assert response.json()["username"] == "alice"


def test_session_user_can_create_and_list_a_token_without_exposing_secrets(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    created = db_client.post("/api/auth/tokens", json={"name": "CLI"})
    listed = db_client.get("/api/auth/tokens")

    assert created.status_code == 201
    assert set(created.json()) == {"id", "name", "scope", "created_at", "token"}
    assert created.json()["name"] == "CLI"
    assert created.json()["scope"] == "read"
    assert created.json()["token"]
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "id": created.json()["id"],
            "name": "CLI",
            "scope": "read",
            "created_at": created.json()["created_at"],
        }
    ]
    serialized = json.dumps(listed.json())
    assert "token" not in serialized
    assert "token_hash" not in serialized
    assert created.json()["token"] not in serialized


def test_token_list_only_contains_the_session_users_tokens(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db, "alice")
    create_user(migrated_db, "bob")
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    alice_token = db_client.post("/api/auth/tokens", json={"name": "Alice CLI"}).json()
    db_client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    bob_token = db_client.post(
        "/api/auth/tokens", json={"name": "Bob CLI", "scope": "read_write"}
    ).json()

    response = db_client.get("/api/auth/tokens")

    assert response.json() == [
        {
            "id": bob_token["id"],
            "name": "Bob CLI",
            "scope": "read_write",
            "created_at": bob_token["created_at"],
        }
    ]
    assert alice_token["id"] not in response.text


def test_token_scope_rejects_values_outside_the_declared_literal(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    for scope in ("admin", "write"):
        response = db_client.post(
            "/api/auth/tokens", json={"name": "invalid", "scope": scope}
        )
        assert response.status_code == 422


def test_issued_token_authenticates_until_the_owner_revokes_it(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    issued = db_client.post("/api/auth/tokens", json={"name": "CLI"}).json()
    headers = {"Authorization": f"Bearer {issued['token']}"}
    db_client.cookies.clear()

    assert db_client.get("/api/auth/me", headers=headers).json()["username"] == "alice"

    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    revoked = db_client.delete(f"/api/auth/tokens/{issued['id']}")
    db_client.cookies.clear()

    assert revoked.status_code == 204
    assert db_client.get("/api/auth/me", headers=headers).json()["authenticated"] is False


def test_revoking_another_users_or_unknown_token_returns_not_found_without_revoking_it(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db, "alice")
    create_user(migrated_db, "bob")
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    issued = db_client.post("/api/auth/tokens", json={"name": "Alice CLI"}).json()
    headers = {"Authorization": f"Bearer {issued['token']}"}
    db_client.post("/api/auth/login", json={"username": "bob", "password": "secret"})

    assert db_client.delete(f"/api/auth/tokens/{issued['id']}").status_code == 404
    assert db_client.delete(f"/api/auth/tokens/{uuid4()}").status_code == 404
    db_client.cookies.clear()
    assert db_client.get("/api/auth/me", headers=headers).json()["username"] == "alice"


def test_token_management_endpoints_require_a_session_even_for_read_write_tokens(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    token = issue_token(migrated_db, "alice", scope="read_write")
    headers = {"Authorization": f"Bearer {token}"}
    token_id = uuid4()

    assert db_client.post(
        "/api/auth/tokens", json={"name": "nested"}, headers=headers
    ).status_code == 403
    assert db_client.get("/api/auth/tokens", headers=headers).status_code == 403
    assert db_client.delete(f"/api/auth/tokens/{token_id}", headers=headers).status_code == 403


def test_token_management_endpoints_reject_anonymous_requests(
    db_client: TestClient,
):
    token_id = uuid4()

    assert db_client.post("/api/auth/tokens", json={"name": "anonymous"}).status_code == 401
    assert db_client.get("/api/auth/tokens").status_code == 401
    assert db_client.delete(f"/api/auth/tokens/{token_id}").status_code == 401


def test_password_change_expires_the_cookie_and_only_the_new_password_logs_in(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    changed = db_client.put(
        "/api/auth/password",
        json={"current_password": "secret", "new_password": "new-secret"},
    )

    assert changed.status_code == 200
    assert changed.json() == {"authenticated": False, "username": None, "is_admin": False}
    assert db_client.get("/api/auth/me").json()["authenticated"] is False
    assert (
        db_client.post(
            "/api/auth/login", json={"username": "alice", "password": "secret"}
        ).status_code
        == 401
    )
    assert (
        db_client.post(
            "/api/auth/login", json={"username": "alice", "password": "new-secret"}
        ).status_code
        == 200
    )


def test_password_change_invalidates_the_sessions_opened_elsewhere(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    elsewhere = db_client.cookies[SESSION_COOKIE]
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    db_client.put(
        "/api/auth/password",
        json={"current_password": "secret", "new_password": "new-secret"},
    )

    db_client.cookies.clear()
    db_client.cookies.set(SESSION_COOKIE, elsewhere)
    assert db_client.get("/api/auth/me").json()["authenticated"] is False


def test_password_change_rejects_a_wrong_current_password_and_keeps_the_session(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    response = db_client.put(
        "/api/auth/password",
        json={"current_password": "wrong", "new_password": "new-secret"},
    )

    assert response.status_code == 403
    assert db_client.get("/api/auth/me").json()["username"] == "alice"
    assert (
        db_client.post(
            "/api/auth/login", json={"username": "alice", "password": "secret"}
        ).status_code
        == 200
    )


def test_password_change_requires_a_session_and_rejects_a_read_write_token(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    token = issue_token(migrated_db, "alice", scope="read_write")
    body = {"current_password": "secret", "new_password": "new-secret"}

    anonymous = db_client.put("/api/auth/password", json=body)
    with_token = db_client.put(
        "/api/auth/password", json=body, headers={"Authorization": f"Bearer {token}"}
    )

    assert anonymous.status_code == 401
    assert with_token.status_code == 403
    assert (
        db_client.post(
            "/api/auth/login", json={"username": "alice", "password": "secret"}
        ).status_code
        == 200
    )


def test_password_change_rejects_an_empty_new_password(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})

    response = db_client.put(
        "/api/auth/password", json={"current_password": "secret", "new_password": ""}
    )

    assert response.status_code == 422
    assert db_client.get("/api/auth/me").json()["username"] == "alice"


def test_password_change_leaves_the_api_tokens_usable(
    db_client: TestClient, migrated_db: str
):
    create_user(migrated_db)
    db_client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    issued = db_client.post("/api/auth/tokens", json={"name": "CLI"}).json()

    db_client.put(
        "/api/auth/password",
        json={"current_password": "secret", "new_password": "new-secret"},
    )

    db_client.cookies.clear()
    me = db_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {issued['token']}"}
    )
    assert me.json()["username"] == "alice"
