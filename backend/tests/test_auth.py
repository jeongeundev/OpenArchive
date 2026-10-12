import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import login_as

from openarchive.services import auth as auth_service
from openarchive.services.auth import (
    MAX_LOGIN_FAILURES,
    MIN_PASSWORD_LENGTH,
    AuthenticationFailed,
    CannotDeleteSelf,
    InvalidUsername,
    LoginLocked,
    PasswordTooShort,
    TokenNotFound,
    UserNotFound,
    admin_exists,
    authenticate_user,
    change_password,
    create_session,
    create_token,
    create_user,
    delete_user,
    hash_password,
    insert_token,
    list_tokens,
    logout,
    reset_password,
    revoke_token,
    validate_session,
    validate_token,
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


async def test_logout_revokes_only_the_given_session_not_the_others(conn):
    user_id = await _create_user(conn)
    revoked_token = await create_session(conn, user_id)
    remaining_token = await create_session(conn, user_id)

    await logout(conn, revoked_token)

    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, revoked_token)
    remaining_user = await validate_session(conn, remaining_token)
    assert remaining_user["id"] == user_id


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


async def test_api_token_is_stored_only_as_a_sha256_hash(conn):
    user_id = await _create_user(conn)

    issued = await create_token(conn, user_id, name="automation")
    stored = await (
        await conn.execute(
            "SELECT name, token_hash, scope FROM api_tokens WHERE id = %s", (issued["id"],)
        )
    ).fetchone()

    assert issued["token"] not in stored
    assert stored[1] == hashlib.sha256(issued["token"].encode()).hexdigest()


async def test_api_tokens_are_unique_even_with_the_same_name(conn):
    user_id = await _create_user(conn)

    first = await create_token(conn, user_id, name="automation")
    second = await create_token(conn, user_id, name="automation")

    assert first["token"] != second["token"]


@pytest.mark.parametrize("scope", ["read", "read_write"])
async def test_valid_api_token_returns_its_user_scope_and_credential(conn, scope):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="automation", scope=scope)

    user = await validate_token(conn, issued["token"])

    assert user == {
        "id": user_id,
        "username": "alice",
        "is_admin": False,
        "scope": scope,
        "credential": "token",
        "kind": "user",
        "principal": "alice",
        "expires_at": None,
    }


async def test_a_share_token_resolves_to_the_share_principal(conn):
    owner_id = await _create_user(conn)
    share_id = (
        await (
            await conn.execute(
                "INSERT INTO shares (owner_user_id, name) VALUES (%s, 'B사') RETURNING id",
                (owner_id,),
            )
        ).fetchone()
    )[0]
    issued = await insert_token(conn, user_id=None, share_id=share_id, name="B사", scope="read")

    principal = await validate_token(conn, issued["token"])

    assert principal == {
        "kind": "share",
        "share_id": share_id,
        "principal": f"share:{share_id}",
        "username": None,
        "is_admin": False,
        "scope": "read",
        "credential": "token",
        "expires_at": None,
    }


async def test_valid_session_returns_the_common_credential_shape(conn):
    user_id = await _create_user(conn)
    token = await create_session(conn, user_id)

    user = await validate_session(conn, token)

    assert user == {
        "id": user_id,
        "username": "alice",
        "is_admin": False,
        "scope": "read_write",
        "credential": "session",
        "kind": "user",
        "principal": "alice",
    }


@pytest.mark.parametrize("token", ["missing-token", ""])
async def test_missing_or_empty_api_token_is_rejected(conn, token):
    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, token)


async def test_revoked_api_token_is_rejected(conn):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="automation")

    await revoke_token(conn, issued["id"], user_id=user_id)

    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, issued["token"])


async def test_revoking_one_token_does_not_revoke_other_credentials(conn):
    user_id = await _create_user(conn)
    revoked = await create_token(conn, user_id, name="first")
    remaining = await create_token(conn, user_id, name="second")
    session = await create_session(conn, user_id)

    await revoke_token(conn, revoked["id"], user_id=user_id)

    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, revoked["token"])
    assert (await validate_token(conn, remaining["token"]))["id"] == user_id
    assert (await validate_session(conn, session))["id"] == user_id


async def test_user_cannot_revoke_another_users_token(conn):
    alice_id = await _create_user(conn)
    alice_token = await create_token(conn, alice_id, name="alice-token")
    bob_id = (
        await (
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES ('bob', %s) RETURNING id",
                (hash_password("bob-secret"),),
            )
        ).fetchone()
    )[0]

    with pytest.raises(TokenNotFound):
        await revoke_token(conn, alice_token["id"], user_id=bob_id)

    assert (await validate_token(conn, alice_token["token"]))["id"] == alice_id


async def test_list_tokens_returns_only_safe_metadata_for_the_user(conn):
    alice_id = await _create_user(conn)
    alice_token = await create_token(conn, alice_id, name="alice-token")
    bob_id = (
        await (
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES ('bob', %s) RETURNING id",
                (hash_password("bob-secret"),),
            )
        ).fetchone()
    )[0]
    await create_token(conn, bob_id, name="bob-token")

    listed = await list_tokens(conn, alice_id)

    assert [item["name"] for item in listed] == ["alice-token"]
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]
    assert alice_token["token"] not in str(listed)


async def test_deleting_a_user_invalidates_all_api_tokens(conn):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="automation")

    await conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    with pytest.raises(AuthenticationFailed):
        await validate_token(conn, issued["token"])
    remaining = await (
        await conn.execute("SELECT count(*) FROM api_tokens WHERE user_id = %s", (user_id,))
    ).fetchone()
    assert remaining == (0,)


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


def test_admin_api_rejects_an_admins_read_write_token(db_client, migrated_db):
    login_as(db_client, "alice")
    token = "admin-read-write-token"
    with psycopg.connect(migrated_db) as conn:
        user_id = conn.execute(
            "UPDATE users SET is_admin = true WHERE username = 'alice' RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, scope) VALUES (%s, 'admin-test', %s, 'read_write')",
            (user_id, hashlib.sha256(token.encode()).hexdigest()),
        )

    response = db_client.get(
        "/api/admin/users", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


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


async def test_change_password_rejects_a_wrong_current_password(conn):
    user_id = await _create_user(conn, "correct horse")

    with pytest.raises(AuthenticationFailed):
        await change_password(
            conn, user_id, current_password="wrong horse", new_password="new staple"
        )

    assert (await authenticate_user(conn, "alice", "correct horse"))["id"] == user_id


async def test_change_password_replaces_the_hash_so_only_the_new_password_works(conn):
    user_id = await _create_user(conn, "correct horse")

    await change_password(
        conn, user_id, current_password="correct horse", new_password="new staple"
    )

    assert (await authenticate_user(conn, "alice", "new staple"))["id"] == user_id
    with pytest.raises(AuthenticationFailed):
        await authenticate_user(conn, "alice", "correct horse")


async def test_change_password_invalidates_every_session_of_that_user_only(conn):
    alice_id = await _create_user(conn, "correct horse")
    bob_id = (
        await (
            await conn.execute(
                "INSERT INTO users (username, password_hash) VALUES ('bob', %s) RETURNING id",
                (hash_password("bob secret"),),
            )
        ).fetchone()
    )[0]
    laptop = await create_session(conn, alice_id)
    phone = await create_session(conn, alice_id)
    bob_session = await create_session(conn, bob_id)

    await change_password(
        conn, alice_id, current_password="correct horse", new_password="new staple"
    )

    for revoked in (laptop, phone):
        with pytest.raises(AuthenticationFailed):
            await validate_session(conn, revoked)
    assert (await validate_session(conn, bob_session))["id"] == bob_id


async def test_change_password_keeps_the_api_tokens_it_did_not_revoke(conn):
    """토큰은 비밀번호와 독립한 위임 자격증명이다 (ADR-034). 폐기는 목록에서 개별로 한다."""
    user_id = await _create_user(conn, "correct horse")
    issued = await create_token(conn, user_id, name="automation")

    await change_password(
        conn, user_id, current_password="correct horse", new_password="new staple"
    )

    assert (await validate_token(conn, issued["token"]))["id"] == user_id


async def test_reset_password_replaces_the_hash_without_the_current_one(conn):
    user_id = await _create_user(conn, "forgotten")
    session = await create_session(conn, user_id)

    await reset_password(conn, "alice", "recovered")

    assert (await authenticate_user(conn, "alice", "recovered"))["id"] == user_id
    with pytest.raises(AuthenticationFailed):
        await authenticate_user(conn, "alice", "forgotten")
    with pytest.raises(AuthenticationFailed):
        await validate_session(conn, session)


async def test_reset_password_reports_an_unknown_username(conn):
    with pytest.raises(UserNotFound):
        await reset_password(conn, "nobody", "recovered")


async def test_admin_exists_ignores_regular_accounts(conn):
    """init이 첫 관리자를 만들지 판정한다 — 일반 계정만 있으면 아직 관리자가 없다."""
    assert await admin_exists(conn) is False

    await create_user(conn, "alice", "secret-pw")
    assert await admin_exists(conn) is False

    await create_user(conn, "boss", "secret-pw", is_admin=True)
    assert await admin_exists(conn) is True


@pytest.mark.parametrize("expiry", ["omitted", "none", "future"])
async def test_token_expiry_issue_and_list(conn, expiry):
    user_id = await _create_user(conn)
    future = (await (await conn.execute("SELECT now() + interval '1 hour'")).fetchone())[0]
    kwargs = {} if expiry == "omitted" else {"expires_at": future if expiry == "future" else None}
    issued = await create_token(conn, user_id, name="expiry", **kwargs)
    expected = future if expiry == "future" else None
    assert issued["expires_at"] == expected
    assert issued["last_used_at"] is None
    assert issued["expired"] is False
    await validate_token(conn, issued["token"])
    listed = (await list_tokens(conn, user_id))[0]
    assert listed["expires_at"] == expected
    assert listed["last_used_at"] is not None
    assert listed["expired"] is False
    assert "token" not in listed and "token_hash" not in listed


@pytest.mark.parametrize("offset", ["0 seconds", "-1 second"])
async def test_token_expiry_rejects_nonfuture(conn, offset):
    from openarchive.services.auth import InvalidTokenExpiry

    user_id = await _create_user(conn)
    async with conn.transaction():
        expiry = (await (await conn.execute("SELECT now() + %s::interval", (offset,))).fetchone())[0]
        with pytest.raises(InvalidTokenExpiry, match="만료일은 지금 이후여야 합니다."):
            await create_token(conn, user_id, name="invalid", expires_at=expiry)
        assert (await (await conn.execute("SELECT count(*) FROM api_tokens")).fetchone())[0] == 0


async def test_token_expiry_rejects_expired_and_bad_without_usage(conn):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="expired")
    other = await create_token(conn, user_id, name="untouched")
    await conn.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = %s", (issued["id"],))
    for token in (issued["token"], "bad-token"):
        with pytest.raises(AuthenticationFailed):
            await validate_token(conn, token)
    listed = {t["id"]: t for t in await list_tokens(conn, user_id)}
    assert listed[issued["id"]]["expired"] is True
    assert listed[other["id"]]["expired"] is False
    assert all(t["last_used_at"] is None for t in listed.values())


async def test_token_usage_commit_and_minute_throttle(conn, migrated_db):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="usage")
    async with await psycopg.AsyncConnection.connect(migrated_db) as request:
        await validate_token(request, issued["token"])
    first = (await list_tokens(conn, user_id))[0]["last_used_at"]
    assert first is not None
    await validate_token(conn, issued["token"])
    assert (await list_tokens(conn, user_id))[0]["last_used_at"] == first
    await conn.execute("UPDATE api_tokens SET last_used_at = now() - interval '2 minutes' WHERE id = %s", (issued["id"],))
    old = (await list_tokens(conn, user_id))[0]["last_used_at"]
    await validate_token(conn, issued["token"])
    assert (await list_tokens(conn, user_id))[0]["last_used_at"] > old


async def test_token_usage_skips_locked_row(conn, migrated_db):
    user_id = await _create_user(conn)
    issued = await create_token(conn, user_id, name="parallel")
    await conn.execute("UPDATE api_tokens SET last_used_at = now() - interval '2 minutes' WHERE id = %s", (issued["id"],))
    old = (await list_tokens(conn, user_id))[0]["last_used_at"]
    async with conn.transaction():
        await conn.execute("SELECT id FROM api_tokens WHERE id = %s FOR UPDATE", (issued["id"],))
        async with await psycopg.AsyncConnection.connect(migrated_db) as request:
            await request.execute("SET LOCAL lock_timeout = '1s'")
            principal = await validate_token(request, issued["token"])
            assert principal["username"] == "alice"
    assert (await list_tokens(conn, user_id))[0]["last_used_at"] == old


# --- 비밀번호 최소 길이·사용자명 검증 (ADR-040 2026-10-12 개정) ---


async def _password_hash(conn, username: str = "alice") -> str:
    return (
        await (
            await conn.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
        ).fetchone()
    )[0]


def test_minimum_password_length_is_eight():
    """NIST SP 800-63B의 최소 길이. 구성 규칙(대문자·특수문자)은 두지 않는다."""
    assert MIN_PASSWORD_LENGTH == 8


async def test_create_user_rejects_a_password_shorter_than_eight(conn):
    with pytest.raises(PasswordTooShort, match="비밀번호는 8자 이상이어야 합니다."):
        await create_user(conn, "alice", "1234567")

    assert await list_users_named(conn, "alice") == 0
    await create_user(conn, "alice", "12345678")
    assert await list_users_named(conn, "alice") == 1


async def list_users_named(conn, username: str) -> int:
    return (
        await (
            await conn.execute("SELECT count(*) FROM users WHERE username = %s", (username,))
        ).fetchone()
    )[0]


async def test_change_password_rejects_a_short_new_password_and_keeps_the_old_one(conn):
    user_id = await _create_user(conn, "correct horse")
    session = await create_session(conn, user_id)

    with pytest.raises(PasswordTooShort):
        await change_password(
            conn, user_id, current_password="correct horse", new_password="short"
        )

    assert verify_password("correct horse", await _password_hash(conn))
    assert (await validate_session(conn, session))["id"] == user_id


async def test_reset_password_rejects_a_short_new_password_and_keeps_the_old_one(conn):
    await _create_user(conn, "forgotten")

    with pytest.raises(PasswordTooShort):
        await reset_password(conn, "alice", "short")

    assert verify_password("forgotten", await _password_hash(conn))


async def test_login_does_not_check_the_length_of_an_existing_password(conn):
    """기존 계정은 최소 길이 이전에 만든 짧은 비밀번호로도 계속 로그인한다."""
    user_id = await _create_user(conn, "short")

    assert (await authenticate_user(conn, "alice", "short"))["id"] == user_id


@pytest.mark.parametrize(
    "username", ["", "   ", " alice", "alice ", "\talice", "a" * 65],
    ids=["empty", "blank", "leading", "trailing", "tab", "too-long"],
)
async def test_create_user_rejects_an_invalid_username(conn, username):
    with pytest.raises(InvalidUsername):
        await create_user(conn, username, "long-enough")

    assert (await (await conn.execute("SELECT count(*) FROM users")).fetchone())[0] == 0


async def test_create_user_accepts_a_64_character_username_with_inner_spaces(conn):
    await create_user(conn, "a" * 64, "long-enough")
    await create_user(conn, "kim min", "long-enough")

    assert (await (await conn.execute("SELECT count(*) FROM users")).fetchone())[0] == 2


# --- 로그인 실패 제한 (ADR-040 2026-10-12 개정) ---


async def _fail(conn, username: str, times: int) -> None:
    for _ in range(times):
        with pytest.raises(AuthenticationFailed):
            await authenticate_user(conn, username, "wrong password")


async def _attempts(conn, username: str):
    return await (
        await conn.execute(
            "SELECT failures, locked_until > now() FROM login_attempts WHERE username = %s",
            (username,),
        )
    ).fetchone()


def test_lockout_threshold_is_ten_failures():
    """Microsoft Entra smart lockout 기본값 — 10회 실패 뒤 60초."""
    assert MAX_LOGIN_FAILURES == 10


async def test_ten_failures_lock_the_account_even_against_the_right_password(conn):
    await _create_user(conn, "correct horse")

    await _fail(conn, "alice", MAX_LOGIN_FAILURES)

    with pytest.raises(LoginLocked) as locked:
        await authenticate_user(conn, "alice", "correct horse")
    assert 0 < locked.value.retry_after <= 60
    assert await _attempts(conn, "alice") == (MAX_LOGIN_FAILURES, True)


async def test_nine_failures_do_not_lock_and_a_success_clears_the_count(conn):
    user_id = await _create_user(conn, "correct horse")

    await _fail(conn, "alice", MAX_LOGIN_FAILURES - 1)
    assert await _attempts(conn, "alice") == (MAX_LOGIN_FAILURES - 1, None)

    assert (await authenticate_user(conn, "alice", "correct horse"))["id"] == user_id
    assert await _attempts(conn, "alice") is None


async def test_an_expired_lock_lets_the_right_password_in_and_clears_the_row(conn):
    user_id = await _create_user(conn, "correct horse")
    await _fail(conn, "alice", MAX_LOGIN_FAILURES)
    await conn.execute(
        "UPDATE login_attempts SET locked_until = now() - interval '1 second'"
    )

    assert (await authenticate_user(conn, "alice", "correct horse"))["id"] == user_id
    assert await _attempts(conn, "alice") is None


async def test_a_failure_after_an_expired_lock_locks_again_at_once(conn):
    """잠금이 풀려도 카운터는 남는다 — 그 뒤로는 실패 한 번마다 다시 60초 (사실상 분당 1회)."""
    await _create_user(conn, "correct horse")
    await _fail(conn, "alice", MAX_LOGIN_FAILURES)
    await conn.execute(
        "UPDATE login_attempts SET locked_until = now() - interval '1 second'"
    )

    await _fail(conn, "alice", 1)

    assert await _attempts(conn, "alice") == (MAX_LOGIN_FAILURES + 1, True)
    with pytest.raises(LoginLocked):
        await authenticate_user(conn, "alice", "correct horse")


async def test_an_unknown_username_is_counted_and_locked_the_same_way(conn):
    """잠금 여부로 계정이 있는지 알 수 없어야 한다."""
    await _fail(conn, "nobody", MAX_LOGIN_FAILURES)

    with pytest.raises(LoginLocked):
        await authenticate_user(conn, "nobody", "anything")
    assert await _attempts(conn, "nobody") == (MAX_LOGIN_FAILURES, True)


async def test_failures_are_counted_per_username(conn):
    await _create_user(conn, "correct horse")
    await _fail(conn, "nobody", MAX_LOGIN_FAILURES)

    assert (await authenticate_user(conn, "alice", "correct horse"))["username"] == "alice"


async def test_an_unknown_username_still_computes_one_password_hash(conn, monkeypatch):
    """없는 사용자명에서 scrypt를 건너뛰면 응답 시간으로 계정 존재가 드러난다."""
    calls = []
    real = auth_service.verify_password

    def counting(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(auth_service, "verify_password", counting)

    with pytest.raises(AuthenticationFailed):
        await authenticate_user(conn, "nobody", "anything")

    assert len(calls) == 1
    # 비용 파라미터가 실제 해시와 같아야 걸리는 시간이 같다.
    assert calls[0].split("$")[:4] == hash_password("x").split("$")[:4]


async def test_concurrent_failures_are_all_counted(conn, migrated_db):
    """증가와 잠금 판정은 한 문장 UPSERT다 — 읽고 쓰는 두 문장이면 동시 실패가 사라진다."""
    await _create_user(conn, "correct horse")

    async def one_failure():
        async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
            with pytest.raises(AuthenticationFailed):
                await authenticate_user(c, "alice", "wrong password")

    await asyncio.gather(*(one_failure() for _ in range(MAX_LOGIN_FAILURES)))

    assert await _attempts(conn, "alice") == (MAX_LOGIN_FAILURES, True)


async def test_reset_password_clears_the_lock(conn):
    """운영자가 풀어 주는 경로다 — 재설정한 비밀번호로 바로 들어온다."""
    user_id = await _create_user(conn, "forgotten")
    await _fail(conn, "alice", MAX_LOGIN_FAILURES)

    await reset_password(conn, "alice", "recovered")

    assert await _attempts(conn, "alice") is None
    assert (await authenticate_user(conn, "alice", "recovered"))["id"] == user_id


# --- 관리자 자기 삭제 금지 (ADR-040 2026-10-12 개정) ---


async def test_an_admin_cannot_delete_their_own_account(conn):
    admin = await create_user(conn, "boss", "boss-secret", is_admin=True)

    with pytest.raises(CannotDeleteSelf, match="자기 계정은 삭제할 수 없습니다."):
        await delete_user(conn, admin["id"], actor="boss")

    assert await list_users_named(conn, "boss") == 1


async def test_an_admin_can_still_delete_another_account(conn):
    await create_user(conn, "boss", "boss-secret", is_admin=True)
    other = await create_user(conn, "alice", "alice-secret")

    await delete_user(conn, other["id"], actor="boss")

    assert await list_users_named(conn, "alice") == 0
