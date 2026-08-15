from collections.abc import AsyncIterator
from typing import Annotated

import psycopg
from fastapi import Cookie, Depends, Header, HTTPException, Request

from app.db import get_pool
from app.embeddings.base import EmbeddingProvider
from app.services.auth import (
    CREDENTIAL_SESSION,
    SCOPE_READ_WRITE,
    AuthenticationFailed,
    validate_session,
    validate_token,
)

SESSION_COOKIE = "openarchive_session"
BEARER_PREFIX = "Bearer "


async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """요청 하나에 풀 커넥션 하나를 빌려준다."""
    async with get_pool().connection() as conn:
        yield conn


Connection = Annotated[psycopg.AsyncConnection, Depends(get_conn)]


async def current_user(
    conn: Connection,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> dict | None:
    """Bearer 토큰을 우선 해석하고, 없을 때만 쿠키 세션을 본다. 무효면 익명이다.

    Bearer가 아닌 스킴은 이 앱을 향한 토큰이 아니므로 쿠키 판정으로 넘어간다.
    """
    if authorization is not None and authorization.lower().startswith(
        BEARER_PREFIX.lower()
    ):
        try:
            return await validate_token(conn, authorization[len(BEARER_PREFIX) :])
        except AuthenticationFailed:
            return None
    if token is None:
        return None
    try:
        return await validate_session(conn, token)
    except AuthenticationFailed:
        return None


async def require_user_id(user: Annotated[dict | None, Depends(current_user)]) -> str:
    """로그인을 요구하는 요청의 인증된 사용자명을 반환한다."""
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user["username"]


async def require_write_user_id(
    user: Annotated[dict | None, Depends(current_user)],
) -> str:
    """쓰기를 요구하는 요청의 인증된 사용자명을 반환한다. read scope 토큰은 거부한다."""
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if user["scope"] != SCOPE_READ_WRITE:
        raise HTTPException(status_code=403, detail="쓰기 권한이 필요합니다.")
    return user["username"]


async def require_session_user(
    user: Annotated[dict | None, Depends(current_user)],
) -> dict:
    """토큰으로는 열 수 없는 경계. 로그인 세션으로만 통과한다."""
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if user["credential"] != CREDENTIAL_SESSION:
        raise HTTPException(status_code=403, detail="로그인 세션이 필요합니다.")
    return user


async def require_admin(
    user: Annotated[dict | None, Depends(current_user)],
) -> dict:
    """계정 관리 권한을 요구한다. 문서 열람 권한은 확장하지 않는다."""
    user = await require_session_user(user)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """앱 lifespan에서 만든 임베딩 프로바이더를 반환한다."""
    return request.app.state.provider
