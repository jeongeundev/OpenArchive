"""`app.db`의 import 부작용 부재를 검증한다 (ADR-012).

MCP 서버는 `app.services`를 직접 import하고(ADR-008) 워커도 같은 패키지를 쓴다.
import가 곧 접속이면 세 프로세스가 의도치 않게 각자 풀을 연다.
그래서 여기서는 "접속이 되는가"가 아니라 **"import만으로는 아무 일도 일어나지 않는가"**를 본다.
DB 컨테이너 없이 통과해야 한다.
"""

import importlib

import psycopg_pool
import pytest
from psycopg_pool import AsyncConnectionPool

import app.db
from app.config import get_settings


@pytest.fixture(autouse=True)
def _restore_db_module():
    """모듈 전역 상태(_pool)와 패치된 클래스가 테스트 간에 새지 않게 되돌린다."""
    yield
    importlib.reload(app.db)


@pytest.fixture
def pool_spy(monkeypatch):
    """AsyncConnectionPool의 인스턴스화를 관찰한다.

    "풀이 언제 만들어지는가"가 이 모듈의 검증 대상이므로, 생성 자체를 세는 것이
    가장 직접적이다. 패치 후 reload해야 모듈이 스파이 클래스를 집어간다.
    """
    created = []

    class SpyPool:
        @staticmethod
        async def check_connection(_connection):
            pass

        def __init__(self, conninfo="", **kwargs):
            self.conninfo = conninfo
            self.kwargs = kwargs
            created.append(self)

        async def close(self):
            pass

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", SpyPool)
    importlib.reload(app.db)
    return created


def test_import_alone_creates_no_pool(monkeypatch, pool_spy):
    """DATABASE_URL이 없어도 import가 성공하고, 그것만으로 풀이 만들어지지 않는다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    importlib.reload(app.db)

    assert pool_spy == []
    assert app.db._pool is None


def test_get_pool_creates_lazily_and_reuses(pool_spy):
    assert pool_spy == []

    pool = app.db.get_pool()

    assert len(pool_spy) == 1
    assert app.db.get_pool() is pool
    assert len(pool_spy) == 1


def test_pool_is_created_with_auto_open_disabled(pool_spy):
    """psycopg_pool은 기본적으로 생성 시점에 열린다. 껐는지 확인한다.

    켜져 있으면 get_pool()을 부르는 순간 커넥션이 생겨, 여는 시점을 호출부가
    통제할 수 없게 된다.
    """
    app.db.get_pool()

    assert pool_spy[0].kwargs["open"] is False


def test_pool_checks_connections_when_borrowed(pool_spy):
    app.db.get_pool()

    assert pool_spy[0].kwargs["check"] is app.db.AsyncConnectionPool.check_connection


def test_pool_uses_dsn_from_settings(monkeypatch, pool_spy):
    """DSN은 환경변수로만 주입된다 — 코드에 박지 않는다 (ADR-006)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@openproxy.example:6432/pool_a")
    get_settings.cache_clear()

    app.db.get_pool()

    assert pool_spy[0].conninfo == "postgresql://app@openproxy.example:6432/pool_a"


def test_real_pool_is_not_open_on_creation():
    """스파이가 아닌 실제 AsyncConnectionPool로도 확인한다."""
    pool = app.db.get_pool()

    assert isinstance(pool, AsyncConnectionPool)
    assert pool.closed


async def test_close_pool_resets_state(pool_spy):
    pool = app.db.get_pool()

    await app.db.close_pool()

    assert app.db._pool is None
    assert app.db.get_pool() is not pool
    assert len(pool_spy) == 2


async def test_close_pool_without_a_pool_is_noop():
    """풀을 만든 적 없는 프로세스가 종료돼도 예외가 나지 않아야 한다."""
    await app.db.close_pool()

    assert app.db._pool is None
