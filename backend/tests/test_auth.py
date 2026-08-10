from datetime import UTC, datetime, timedelta

import psycopg
import pytest

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
