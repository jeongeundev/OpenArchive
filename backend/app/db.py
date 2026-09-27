"""커넥션 풀만 제공한다. import 시 부작용이 없어야 한다 (ADR-012).

MCP 서버와 워커가 이 패키지를 함께 쓰므로, import가 곧 접속이 되면 세 프로세스가
각자 풀을 연다. 그래서 풀은 `get_pool()`을 처음 부를 때 만들고, 실제로 커넥션을
여는 것은 호출부가 `await pool.open()`으로 명시한다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

# 죽은 연결을 약 60초 안에 감지한다 (ADR-048 결정 1). VIP가 원래 노드로 돌아가는 순간
# 응답을 기다리던 연결은 FIN도 RST도 받지 못하고, OS 기본값으로는 약 2시간 뒤에야 풀린다
# (#110 B-1 — 워커 영구 정지). tcp_user_timeout은 리눅스 전용이며 다른 OS에서는 무시된다.
KEEPALIVE_DEFAULTS = {
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 60000,
}

_pool: AsyncConnectionPool | None = None


def keepalive_kwargs(dsn: str) -> dict:
    """DSN에 없는 keepalive 설정만 돌려준다 — DSN에 적힌 값이 기본값을 이긴다.

    DSN 문자열은 바꾸지 않는다. 여전히 환경변수 하나, 호스트 하나다 (ADR-006).
    """
    given = conninfo_to_dict(dsn)
    return {key: value for key, value in KEEPALIVE_DEFAULTS.items() if key not in given}


def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        dsn = get_settings().database_url
        # open=False가 핵심이다. 켜두면 생성 시점에 커넥션이 열려, 여는 시점을
        # 호출부가 통제할 수 없다.
        _pool = AsyncConnectionPool(
            dsn,
            kwargs=keepalive_kwargs(dsn),
            open=False,
            check=AsyncConnectionPool.check_connection,
        )
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """API 요청·MCP 도구 호출 하나에 풀 연결을 빌려준다. DB 오류로 끝나면 닫아 풀이 버리게 한다
    (ADR-048 결정 2).

    OpenProxy가 `BEGIN`에 AllServersDown을 돌려주면 psycopg의 `transaction()` 카운터가
    되돌려지지 않은 채 연결이 IDLE로 남는다. 풀은 IDLE만 보고 받아들이고, 그 연결의 다음
    `transaction()`마다 `AssertionError`가 난다(#110 B-2). 어떤 DB 오류가 연결을 그렇게
    만드는지 가려낼 수 없으므로 DB 오류로 끝난 연결은 전부 버린다. DB 오류가 아닌 예외
    (HTTP 거절 등)로는 버리지 않는다 — 요청마다 새 연결을 열게 된다.
    """
    async with get_pool().connection() as conn:
        try:
            yield conn
        except psycopg.Error:
            await conn.close()
            raise


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
