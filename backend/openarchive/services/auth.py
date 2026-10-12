# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""비밀번호 해시와 서버 측 세션을 관리하는 인증 서비스."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.config import get_settings
from openarchive.services.visibility import share_principal

# 대화형 로그인에 충분히 비싸면서 테스트·데모 호스트의 메모리를 과도하게 쓰지 않는 16MiB 비용이다.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

SCOPE_READ = "read"
SCOPE_READ_WRITE = "read_write"
TokenScope = Literal["read", "read_write"]

CREDENTIAL_SESSION = "session"
CREDENTIAL_TOKEN = "token"

PRINCIPAL_USER = "user"
PRINCIPAL_SHARE = "share"

# NIST SP 800-63B의 최소 길이. 구성 규칙은 두지 않는다 (ADR-040 2026-10-12 개정).
MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 64
# 로그인이 실패 기록을 남기는 사용자명의 상한. 64자 제한 이전 계정이 있을 수 있어 넉넉히 둔다.
MAX_LOGIN_USERNAME_LENGTH = 256
# Microsoft Entra smart lockout 기본값: 연속 10회 실패면 60초 잠근다.
MAX_LOGIN_FAILURES = 10
LOGIN_LOCK_SECONDS = 60


class UserAlreadyExists(Exception):
    """같은 사용자명의 계정이 이미 존재한다."""


class UserNotFound(Exception):
    """대상 사용자가 존재하지 않는다."""


class UserOwnsDocuments(Exception):
    """소유 문서나 만든 폴더가 있는 사용자다."""

    def __init__(self):
        super().__init__("소유한 문서나 만든 폴더가 있어 삭제할 수 없습니다.")


class CannotDeleteSelf(Exception):
    """관리자가 자기 계정을 지우려 했다. 삭제는 관리자만 하므로 마지막 관리자도 이것으로 남는다."""

    def __init__(self) -> None:
        super().__init__("자기 계정은 삭제할 수 없습니다.")


class PasswordTooShort(ValueError):
    """새 비밀번호가 최소 길이보다 짧다."""

    def __init__(self) -> None:
        super().__init__(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")


class InvalidUsername(ValueError):
    """사용자명이 비었거나 앞뒤에 공백이 있거나 너무 길다."""

    def __init__(self, message: str = "사용자명은 비어 있거나 앞뒤에 공백이 있을 수 없습니다.") -> None:
        super().__init__(message)


class LoginLocked(Exception):
    """연속 실패로 그 사용자명의 로그인이 잠시 잠겼다. 비밀번호가 맞아도 거부한다."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("로그인 시도가 너무 많습니다. 잠시 뒤 다시 시도하세요.")
        self.retry_after = retry_after


class AuthenticationFailed(Exception):
    """자격 증명이나 세션이 유효하지 않은 경우. 세부 실패 사유는 외부에 노출하지 않는다."""

    def __init__(self) -> None:
        super().__init__("인증에 실패했습니다.")


class InvalidTokenExpiry(ValueError):
    """만료일이 지금 이후가 아니다."""

    def __init__(self) -> None:
        super().__init__("만료일은 지금 이후여야 합니다.")


class TokenNotFound(Exception):
    """폐기할 토큰이 없거나 요청한 주체의 것이 아니다."""


def hash_password(password: str) -> str:
    """무작위 salt와 scrypt 비용 파라미터를 함께 저장한다."""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """저장된 파라미터로 다시 계산하고 상수 시간으로 비교한다."""
    try:
        algorithm, n, r, p, encoded_salt, encoded_derived = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_derived.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# 없는 사용자명에도 scrypt를 한 번 돌려 응답 시간으로 계정 존재가 드러나지 않게 한다.
# 비용 파라미터가 실제 해시와 같아야 시간이 같다 — SCRYPT_*를 바꾸면 다시 만든다(테스트가 대조).
# 모듈 import마다 scrypt를 돌리지 않으려고 미리 계산한 고정값이다.
_DUMMY_PASSWORD_HASH = (
    "scrypt$16384$8$1$nz7jsn9ublV8ot7b93wi3g==$"
    "_tjPAEFnujJP-yQcAPpfpqRwloPQxLe_cjB49K-kiFXEy6MM_alNJW5YKp0sa-uIqgRb2cbzAU8aes4YM8Q1jQ=="
)


def _check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort


def _check_username(username: str) -> None:
    if (
        not username
        or username != username.strip()
        or len(username) > MAX_USERNAME_LENGTH
    ):
        raise InvalidUsername
    # 공유 주체의 예약 접두어 — 026의 CHECK가 막지만 그대로 두면 API가 500을 낸다 (ADR-044)
    if username.startswith("share:"):
        raise InvalidUsername("사용자명은 'share:'로 시작할 수 없습니다.")


def hash_token(token: str) -> str:
    """원문 토큰의 sha256 hexdigest. salt를 쓰지 않아 인덱스로 조회된다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_user(
    conn: psycopg.AsyncConnection, username: str, password: str, *, is_admin: bool = False
) -> dict:
    """관리자가 로그인 가능한 계정을 만든다."""
    _check_username(username)
    _check_new_password(password)
    cur = conn.cursor(row_factory=dict_row)
    try:
        await cur.execute(
            """INSERT INTO users (username, password_hash, is_admin)
               VALUES (%s, %s, %s)
               RETURNING id, username, is_admin, created_at""",
            (username, hash_password(password), is_admin),
        )
    except psycopg.errors.UniqueViolation as error:
        raise UserAlreadyExists(f"사용자명 '{username}'은 이미 존재합니다.") from error
    return await cur.fetchone()


async def admin_exists(conn: psycopg.AsyncConnection) -> bool:
    """관리자 계정이 하나라도 있는지. init이 첫 관리자를 만들지 이것으로 판정한다."""
    cur = await conn.execute("SELECT EXISTS (SELECT 1 FROM users WHERE is_admin)")
    return (await cur.fetchone())[0]


async def list_users(conn: psycopg.AsyncConnection) -> list[dict]:
    """비밀번호 해시를 제외한 계정 목록을 반환한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        "SELECT id, username, is_admin, created_at FROM users ORDER BY created_at, id"
    )
    return await cur.fetchall()


class InvalidNewOwner(ValueError):
    """소유권 이전 대상이 없거나 현재 소유자와 같다."""


async def resolve_new_owner(conn, *, current_owner: str, new_owner: str) -> UUID:
    if new_owner == current_owner:
        raise InvalidNewOwner("자기 자신에게는 이전할 수 없습니다.")
    row = await (
        await conn.execute("SELECT id FROM users WHERE username=%s FOR KEY SHARE", (new_owner,))
    ).fetchone()
    if row is None:
        raise InvalidNewOwner("이전받을 사용자를 찾을 수 없습니다.")
    return row[0]


async def delete_user(
    conn: psycopg.AsyncConnection,
    user_id: UUID,
    *,
    transfer_to: str | None = None,
    actor: str | None = None,
) -> None:
    """소유물을 일괄 이전한 뒤 삭제하거나, 이전 대상이 없으면 기존처럼 거부한다.

    `actor`는 삭제를 수행하는 관리자다. 자기에게 옮기면 소유자로서 문서를 읽게 되므로
    거부한다 — 관리자는 이전만 하고 열람을 얻지 않는다(ADR-061 결정 2, ADR-040).
    """
    async with conn.transaction():
        row = await (
            await conn.execute("SELECT username FROM users WHERE id=%s FOR UPDATE", (user_id,))
        ).fetchone()
        if row is None:
            raise UserNotFound
        owner = row[0]
        if actor is not None and owner == actor:
            raise CannotDeleteSelf
        if transfer_to is not None:
            if actor is not None and transfer_to == actor:
                raise InvalidNewOwner("삭제를 수행하는 관리자 자신에게는 이전할 수 없습니다.")
            target_id = await resolve_new_owner(conn, current_owner=owner, new_owner=transfer_to)
            await conn.execute(
                """DELETE FROM document_grants WHERE user_id=%s AND share_id IS NULL
                AND document_id IN (SELECT id FROM documents WHERE owner_id=%s)""",
                (target_id, owner),
            )
            await conn.execute(
                """DELETE FROM folder_grants WHERE user_id=%s
                AND folder_id IN (SELECT id FROM folders WHERE created_by=%s)""",
                (target_id, owner),
            )
            await conn.execute(
                "UPDATE documents SET owner_id=%s, updated_at=now() WHERE owner_id=%s",
                (transfer_to, owner),
            )
            await conn.execute(
                "UPDATE folders SET created_by=%s, updated_at=now() WHERE created_by=%s",
                (transfer_to, owner),
            )
        owns = await (
            await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM documents WHERE owner_id=%s) "
                "OR EXISTS (SELECT 1 FROM folders WHERE created_by=%s)", (owner, owner),
            )
        ).fetchone()
        if owns[0]:
            raise UserOwnsDocuments
        await conn.execute("DELETE FROM users WHERE id=%s", (user_id,))


async def _replace_password(
    conn: psycopg.AsyncConnection, user_id: UUID, new_password: str
) -> None:
    """해시를 갈아끼우고 그 계정의 세션을 전부 무효화한다.

    API 토큰은 남긴다 — ADR-034에서 토큰은 비밀번호와 독립한 위임 자격증명이고,
    폐기 수단이 계정 설정 화면의 목록에 따로 있다 (ADR-040).
    """
    await conn.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (hash_password(new_password), user_id),
    )
    await conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


async def change_password(
    conn: psycopg.AsyncConnection,
    user_id: UUID,
    *,
    current_password: str,
    new_password: str,
) -> None:
    """본인이 현재 비밀번호를 확인받고 바꾼다. 실패하면 아무것도 바꾸지 않는다."""
    _check_new_password(new_password)
    stored = await (
        await conn.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    ).fetchone()
    if stored is None or not verify_password(current_password, stored[0]):
        raise AuthenticationFailed
    await _replace_password(conn, user_id, new_password)


async def reset_password(
    conn: psycopg.AsyncConnection, username: str, new_password: str
) -> None:
    """현재 비밀번호 없이 갈아끼우는 분실 복구 경로. 운영자 CLI 전용이다 (ADR-040).

    로그인 잠금도 함께 푼다 — 운영자가 잠긴 계정을 열어 주는 경로다.
    """
    _check_new_password(new_password)
    row = await (
        await conn.execute("SELECT id FROM users WHERE username = %s", (username,))
    ).fetchone()
    if row is None:
        raise UserNotFound
    await _replace_password(conn, row[0], new_password)
    await conn.execute("DELETE FROM login_attempts WHERE username = %s", (username,))


async def authenticate_user(
    conn: psycopg.AsyncConnection, username: str, password: str
) -> dict:
    """사용자명과 비밀번호를 검증한다. 모든 실패는 같은 예외로 표현한다.

    잠긴 사용자명은 비밀번호를 보지 않고 `LoginLocked`로 거부한다. 같은 사용자명의 시도는
    `login_attempts` 행 잠금으로 한 줄로 세운다 — 잠금 확인과 기록 사이에 동시 요청이 끼면
    잠기기 직전·풀린 직후에 여러 요청이 비밀번호 검사를 받는다. 판정·기록은 자체 트랜잭션
    블록에서 끝내고 예외는 그 뒤에 던진다(블록 안에서 던지면 실패 기록이 롤백된다).
    호출부가 바깥 트랜잭션 안에서 부르면 블록은 savepoint이므로 호출부가 커밋해야 한다
    (ADR-040 2026-10-12 개정).
    """
    if len(username) > MAX_LOGIN_USERNAME_LENGTH:
        # 있을 수 없는 이름이다. 기록하면 btree 키 크기를 넘어 500이 난다 — 시간만 맞추고 거부한다.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationFailed
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO login_attempts (username) VALUES (%s) ON CONFLICT (username) DO NOTHING",
            (username,),
        )
        state = await (
            await conn.execute(
                """SELECT ceil(extract(epoch FROM locked_until - now()))::int
                   FROM login_attempts WHERE username = %s FOR UPDATE""",
                (username,),
            )
        ).fetchone()
        remaining = state[0] if state is not None else None
        if remaining is not None and remaining > 0:
            locked_for = remaining
        else:
            locked_for = None
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT id, username, password_hash, is_admin FROM users WHERE username = %s",
                (username,),
            )
            user = await cur.fetchone()
            stored = _DUMMY_PASSWORD_HASH if user is None else user["password_hash"]
            if not verify_password(password, stored) or user is None:
                user = None
                await _record_login_failure(conn, username)
            else:
                await conn.execute(
                    "DELETE FROM login_attempts WHERE username = %s", (username,)
                )
    if locked_for is not None:
        raise LoginLocked(locked_for)
    if user is None:
        raise AuthenticationFailed
    del user["password_hash"]
    return user


async def _record_login_failure(conn: psycopg.AsyncConnection, username: str) -> None:
    """실패를 세고 문턱에 닿으면 잠근다. 호출부가 그 행의 잠금을 쥔 상태에서 부른다.

    잠금이 풀린 뒤에도 카운터는 남아, 다음 실패에서 곧바로 다시 잠근다.
    """
    await conn.execute(
        """
        UPDATE login_attempts SET
          failures = failures + 1,
          locked_until = CASE WHEN failures + 1 >= %(limit)s
                              THEN now() + make_interval(secs => %(secs)s)
                              ELSE locked_until END
        WHERE username = %(username)s
        """,
        {"username": username, "limit": MAX_LOGIN_FAILURES, "secs": LOGIN_LOCK_SECONDS},
    )


async def create_session(
    conn: psycopg.AsyncConnection,
    user_id: UUID,
    *,
    expires_at: datetime | None = None,
) -> str:
    """서버 측 세션을 발급하고 원문 토큰을 반환한다."""
    token = secrets.token_urlsafe(32)
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=get_settings().session_lifetime_hours)
    await conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
        (token, user_id, expires_at),
    )
    return token


async def validate_session(conn: psycopg.AsyncConnection, token: str) -> dict:
    """만료되지 않은 세션의 사용자를 반환한다."""
    if not token:
        raise AuthenticationFailed
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT u.id, u.username, u.is_admin
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = %s AND s.expires_at > now()
        """,
        (token,),
    )
    user = await cur.fetchone()
    if user is None:
        raise AuthenticationFailed
    user["scope"] = SCOPE_READ_WRITE
    user["credential"] = CREDENTIAL_SESSION
    user["kind"] = PRINCIPAL_USER
    user["principal"] = user["username"]
    return user


async def logout(conn: psycopg.AsyncConnection, token: str) -> None:
    """세션 행을 삭제해 토큰을 즉시 무효화한다."""
    if token:
        await conn.execute("DELETE FROM sessions WHERE token = %s", (token,))


async def create_token(
    conn: psycopg.AsyncConnection,
    user_id: UUID,
    *,
    name: str,
    scope: TokenScope = SCOPE_READ,
    expires_at: datetime | None = None,
) -> dict:
    """토큰을 발급하고 원문을 포함해 반환한다. DB에는 해시만 남는다."""
    return await insert_token(
        conn, user_id=user_id, share_id=None, name=name, scope=scope, expires_at=expires_at
    )


async def insert_token(
    conn: psycopg.AsyncConnection,
    *,
    user_id: UUID | None,
    share_id: UUID | None,
    name: str,
    scope: TokenScope,
    expires_at: datetime | None = None,
) -> dict:
    """사용자 토큰과 공유 토큰의 공통 발급부. 주체는 정확히 하나다(026 CHECK)."""
    token = secrets.token_urlsafe(32)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        INSERT INTO api_tokens (user_id, share_id, name, token_hash, scope, expires_at)
        SELECT %s, %s, %s, %s, %s, %s
        WHERE %s::timestamptz IS NULL OR %s::timestamptz > now()
        RETURNING id, name, scope, created_at, expires_at, last_used_at,
                  expires_at IS NOT NULL AND expires_at <= now() AS expired
        """,
        (user_id, share_id, name, hash_token(token), scope, expires_at, expires_at, expires_at),
    )
    issued = await cur.fetchone()
    if issued is None:
        raise InvalidTokenExpiry
    issued["token"] = token
    return issued


async def validate_token(conn: psycopg.AsyncConnection, token: str) -> dict:
    """유효한 토큰의 주체를 반환한다. 실패는 AuthenticationFailed 하나로 표현한다.

    사용자 토큰은 그 사용자, 공유 토큰은 공유 주체(share:<uuid>)로 해석한다 (ADR-044 「공유」).
    공유 주체에는 사용자명이 없다 — 사용자 전용 경로는 kind로 막는다(api/deps.py).
    """
    if not token:
        raise AuthenticationFailed
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT u.id, u.username, u.is_admin, t.share_id, t.scope, t.expires_at,
               t.id AS token_id
        FROM api_tokens t
        LEFT JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %s
          AND (t.expires_at IS NULL OR t.expires_at > now())
        """,
        (hash_token(token),),
    )
    row = await cur.fetchone()
    if row is None:
        raise AuthenticationFailed
    await conn.execute(
        """
        UPDATE api_tokens SET last_used_at = now()
        WHERE id = (
            SELECT id FROM api_tokens
            WHERE id = %s
              AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')
            FOR UPDATE SKIP LOCKED
        )
        """,
        (row.pop("token_id"),),
    )
    if row["share_id"] is not None:
        return {
            "kind": PRINCIPAL_SHARE,
            "share_id": row["share_id"],
            "principal": share_principal(row["share_id"]),
            "username": None,
            "is_admin": False,
            "scope": row["scope"],
            "credential": CREDENTIAL_TOKEN,
            "expires_at": row["expires_at"],
        }
    del row["share_id"]
    row["credential"] = CREDENTIAL_TOKEN
    row["kind"] = PRINCIPAL_USER
    row["principal"] = row["username"]
    return row


async def list_tokens(conn: psycopg.AsyncConnection, user_id: UUID) -> list[dict]:
    """자기 토큰 목록. 해시도 원문도 포함하지 않는다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT id, name, scope, created_at, expires_at, last_used_at,
               expires_at IS NOT NULL AND expires_at <= now() AS expired
        FROM api_tokens
        WHERE user_id = %s
        ORDER BY created_at, id
        """,
        (user_id,),
    )
    return await cur.fetchall()


async def revoke_token(
    conn: psycopg.AsyncConnection, token_id: UUID, *, user_id: UUID
) -> None:
    """자기 토큰 한 건을 삭제한다. 대상이 없으면 TokenNotFound."""
    deleted = await conn.execute(
        "DELETE FROM api_tokens WHERE id = %s AND user_id = %s", (token_id, user_id)
    )
    if deleted.rowcount == 0:
        raise TokenNotFound
