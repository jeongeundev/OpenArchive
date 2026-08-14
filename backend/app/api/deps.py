from collections.abc import AsyncIterator
from typing import Annotated

import psycopg
from fastapi import Cookie, Depends, HTTPException, Request

from app.db import get_pool
from app.embeddings.base import EmbeddingProvider
from app.services.auth import AuthenticationFailed, validate_session

SESSION_COOKIE = "openarchive_session"


async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """요청 하나에 풀 커넥션 하나를 빌려준다."""
    async with get_pool().connection() as conn:
        yield conn


Connection = Annotated[psycopg.AsyncConnection, Depends(get_conn)]


async def current_user(
    conn: Connection,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> dict | None:
    """유효한 쿠키 세션의 사용자를 반환하며, 없거나 무효면 익명으로 둔다."""
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


async def require_admin(user: Annotated[dict | None, Depends(current_user)]) -> dict:
    """계정 관리 권한을 요구한다. 문서 열람 권한은 확장하지 않는다."""
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """앱 lifespan에서 만든 임베딩 프로바이더를 반환한다."""
    return request.app.state.provider
