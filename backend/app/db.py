"""커넥션 풀만 제공한다. import 시 부작용이 없어야 한다 (ADR-012).

MCP 서버와 워커가 이 패키지를 함께 쓰므로, import가 곧 접속이 되면 세 프로세스가
각자 풀을 연다. 그래서 풀은 `get_pool()`을 처음 부를 때 만들고, 실제로 커넥션을
여는 것은 호출부가 `await pool.open()`으로 명시한다.
"""

from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

_pool: AsyncConnectionPool | None = None


def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        # open=False가 핵심이다. 켜두면 생성 시점에 커넥션이 열려, 여는 시점을
        # 호출부가 통제할 수 없다.
        _pool = AsyncConnectionPool(
            get_settings().database_url,
            open=False,
            check=AsyncConnectionPool.check_connection,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
