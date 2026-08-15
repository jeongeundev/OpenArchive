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

from app.config import get_settings

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


class UserAlreadyExists(Exception):
    """같은 사용자명의 계정이 이미 존재한다."""


class UserNotFound(Exception):
    """삭제할 사용자가 존재하지 않는다."""


class UserOwnsDocuments(Exception):
    """삭제하면 소유 문서가 유령 문서가 되는 사용자다."""


class AuthenticationFailed(Exception):
    """자격 증명이나 세션이 유효하지 않은 경우. 세부 실패 사유는 외부에 노출하지 않는다."""

    def __init__(self) -> None:
        super().__init__("인증에 실패했습니다.")


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


def hash_token(token: str) -> str:
    """원문 토큰의 sha256 hexdigest. salt를 쓰지 않아 인덱스로 조회된다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_user(
    conn: psycopg.AsyncConnection, username: str, password: str, *, is_admin: bool = False
) -> dict:
    """관리자가 로그인 가능한 계정을 만든다."""
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


async def list_users(conn: psycopg.AsyncConnection) -> list[dict]:
    """비밀번호 해시를 제외한 계정 목록을 반환한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        "SELECT id, username, is_admin, created_at FROM users ORDER BY created_at, id"
    )
    return await cur.fetchall()


async def delete_user(conn: psycopg.AsyncConnection, user_id: UUID) -> None:
    """소유 문서가 있으면 삭제를 거부해 owner_id 유령 문서를 만들지 않는다."""
    owns_document = await (
        await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM documents WHERE owner_id = (SELECT username FROM users WHERE id = %s))",
            (user_id,),
        )
    ).fetchone()
    if owns_document[0]:
        raise UserOwnsDocuments
    deleted = await conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
    if deleted.rowcount == 0:
        raise UserNotFound


async def authenticate_user(
    conn: psycopg.AsyncConnection, username: str, password: str
) -> dict:
    """사용자명과 비밀번호를 검증한다. 모든 실패는 같은 예외로 표현한다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        "SELECT id, username, password_hash, is_admin FROM users WHERE username = %s",
        (username,),
    )
    user = await cur.fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        raise AuthenticationFailed
    del user["password_hash"]
    return user


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
) -> dict:
    """토큰을 발급하고 원문을 포함해 반환한다. DB에는 해시만 남는다."""
    token = secrets.token_urlsafe(32)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        INSERT INTO api_tokens (user_id, name, token_hash, scope)
        VALUES (%s, %s, %s, %s)
        RETURNING id, name, scope, created_at
        """,
        (user_id, name, hash_token(token), scope),
    )
    issued = await cur.fetchone()
    issued["token"] = token
    return issued


async def validate_token(conn: psycopg.AsyncConnection, token: str) -> dict:
    """유효한 토큰의 주체를 반환한다. 실패는 AuthenticationFailed 하나로 표현한다."""
    if not token:
        raise AuthenticationFailed
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT u.id, u.username, u.is_admin, t.scope
        FROM api_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %s
        """,
        (hash_token(token),),
    )
    user = await cur.fetchone()
    if user is None:
        raise AuthenticationFailed
    user["credential"] = CREDENTIAL_TOKEN
    return user


async def list_tokens(conn: psycopg.AsyncConnection, user_id: UUID) -> list[dict]:
    """자기 토큰 목록. 해시도 원문도 포함하지 않는다."""
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT id, name, scope, created_at
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
