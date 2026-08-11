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
