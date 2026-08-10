"""비밀번호 해시와 서버 측 세션을 관리하는 인증 서비스."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
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


class AuthenticationFailed(Exception):
    """자격 증명이나 세션이 유효하지 않은 경우. 세부 실패 사유는 외부에 노출하지 않는다."""

    def __init__(self) -> None:
        super().__init__("인증에 실패했습니다.")


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
    return user


async def logout(conn: psycopg.AsyncConnection, token: str) -> None:
    """세션 행을 삭제해 토큰을 즉시 무효화한다."""
    if token:
        await conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
