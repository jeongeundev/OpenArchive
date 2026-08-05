from collections.abc import AsyncIterator
from typing import Annotated

import psycopg
from fastapi import Header, HTTPException

from app.db import get_pool


async def get_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """요청 하나에 풀 커넥션 하나를 빌려준다."""
    async with get_pool().connection() as conn:
        yield conn


def require_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """쓰기 요청의 사용자 ID를 반환하며, 헤더가 없으면 거부한다."""
    if x_user_id is None:
        raise HTTPException(status_code=400, detail="X-User-Id 헤더가 필요합니다.")
    return x_user_id


def optional_user_id(x_user_id: Annotated[str | None, Header()] = None) -> str | None:
    """읽기 요청의 선택적 사용자 ID를 반환한다."""
    return x_user_id
