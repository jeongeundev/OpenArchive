import hashlib
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import login_as

from app.services.auth import (
    AuthenticationFailed,
    authenticate_user,
    create_session,
    hash_password,
    logout,
    validate_session,
    verify_password,
)


@pytest.fixture
async def conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


async def _create_user(conn: psycopg.AsyncConnection, password: str = "correct horse"):
    return (
        await (
            await conn.execute(
                """
                INSERT INTO users (username, password_hash)
                VALUES ('alice', %s)
                RETURNING id
                """,
                (hash_password(password),),
            )
        ).fetchone()
    )[0]


def test_same_password_produces_a_different_salted_hash_each_time():
    assert hash_password("same password") != hash_password("same password")


def test_wrong_password_is_rejected():
    stored = hash_password("correct password")

    assert verify_password("wrong password", stored) is False


def test_password_hash_does_not_contain_the_plaintext():
    password = "plain-secret-value"

    assert password not in hash_password(password)


async def test_session_tokens_are_unique(conn):
    user_id = await _create_user(conn)

    first = await create_session(conn, user_id)
    second = await create_session(conn, user_id)

    assert first != second


async def test_expired_session_is_rejected(conn):
    user_id = await _create_user(conn)
    token = await create_session(
        conn,
        user_id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, token)


async def test_logout_immediately_invalidates_the_session(conn):
    user_id = await _create_user(conn)
    token = await create_session(conn, user_id)

    await logout(conn, token)

    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, token)


@pytest.mark.parametrize("token", ["missing-token", ""])
async def test_missing_or_empty_session_token_is_rejected(conn, token):
    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, token)


async def test_deleting_a_user_removes_and_invalidates_their_sessions(conn):
    user_id = await _create_user(conn)
    token = await create_session(conn, user_id)

    await conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, token)

    remaining = await (
        await conn.execute("SELECT count(*) FROM sessions WHERE token = %s", (token,))
    ).fetchone()
    assert remaining == (0,)


async def test_valid_session_returns_its_user(conn):
    user_id = await _create_user(conn)
    token = await create_session(conn, user_id)

    user = await validate_session(conn, token)

    assert user["id"] == user_id
    assert user["username"] == "alice"


async def test_unknown_user_and_wrong_password_share_one_failure(conn):
    await _create_user(conn)

    for username, password in [("nobody", "guess"), ("alice", "guess")]:
        with pytest.raises(AuthenticationFailed, match="인증에 실패했습니다"):
            await authenticate_user(conn, username, password)


def test_anonymous_and_regular_users_cannot_manage_accounts(db_client):
    assert db_client.post(
        "/api/admin/users", json={"username": "new-user", "password": "secret"}
    ).status_code == 401

    login_as(db_client, "alice")
    assert db_client.get("/api/admin/users").status_code == 403


def test_admin_can_create_list_and_login_as_a_new_user(db_client, migrated_db):
    login_as(db_client, "alice")
    with psycopg.connect(migrated_db) as conn:
        conn.execute("UPDATE users SET is_admin = true WHERE username = 'alice'")

    created = db_client.post(
        "/api/admin/users",
        json={"username": "created-user", "password": "created-secret"},
    )
    listed = db_client.get("/api/admin/users")

    assert created.status_code == 201
    assert "password_hash" not in created.json()
    assert "password_hash" not in listed.text
    assert any(user["username"] == "created-user" for user in listed.json())

    db_client.post("/api/auth/logout")
    login = db_client.post(
        "/api/auth/login",
        json={"username": "created-user", "password": "created-secret"},
    )
    assert login.status_code == 200


def test_deleting_user_with_owned_documents_is_rejected(db_client, migrated_db):
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES ('admin', %s, true)",
            (hash_password("admin-secret"),),
        )
        user_id = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES ('owner', %s) RETURNING id",
            (hash_password("owner-secret"),),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO documents (title, content_type, content, content_hash, owner_id, visibility)
               VALUES ('private', 'txt', 'owned', %s, 'owner', 'private')""",
            (hashlib.sha256(b"owned").hexdigest(),),
        )
    response = db_client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-secret"}
    )
    assert response.status_code == 200

    deleted = db_client.delete(f"/api/admin/users/{user_id}")

    assert deleted.status_code == 409
    assert "문서" in deleted.json()["detail"]
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT id FROM users WHERE id = %s", (user_id,)).fetchone() == (
            user_id,
        )
