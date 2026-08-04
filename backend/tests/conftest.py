import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import get_settings
from app.main import app

# 개발 DB(openarchive)와 분리한다. 테스트는 매번 스키마를 통째로 비우므로
# 같은 DB를 쓰면 개발 데이터가 사라진다.
TEST_DB_NAME = "openarchive_test"


def swap_dbname(dsn: str, dbname: str) -> str:
    """DSN에서 데이터베이스 이름만 바꾼다.

    호스트·포트·자격증명을 그대로 물려받으므로, DSN을 OpenProxy VIP(:6432)로
    바꿔도 테스트 하네스는 그대로 동작한다 (ADR-006).
    """
    params = conninfo_to_dict(dsn)
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings()는 캐시되므로, 환경변수를 바꾸는 테스트끼리 서로 오염될 수 있다.

    매 테스트 전후로 비워 각 테스트가 자기가 설정한 환경만 보게 한다.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    # 컨텍스트 매니저로 쓰지 않으므로 lifespan이 돌지 않는다. 의도된 것이다 —
    # 앱 테스트 전체가 DB에 묶이지 않게 한다. lifespan 자체를 보는 테스트는
    # test_main.py에서 `with TestClient(app)`으로 명시적으로 태운다.
    return TestClient(app)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    """테스트 전용 DB를 만들고 그 DSN을 반환한다."""
    admin_dsn = get_settings().database_url
    dsn = swap_dbname(admin_dsn, TEST_DB_NAME)

    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
            ).fetchone()
            if exists is None:
                conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    except psycopg.OperationalError as exc:
        # skip하지 않는다. 트리거·SKIP LOCKED·vector 연산자는 실제 DB에서만
        # 검증되므로, 조용한 skip은 "검증했다"는 거짓 신호가 된다.
        pytest.fail(
            f"테스트 DB에 접속할 수 없습니다: {exc}\n"
            f"  DSN(관리 연결): {admin_dsn}\n"
            "  프로젝트 루트에서 `docker compose up -d` 후 `docker compose ps`로 healthy를 확인하세요.",
            pytrace=False,
        )

    return dsn


@pytest.fixture
def clean_db(test_dsn: str) -> str:
    """스키마가 비워진 테스트 DB의 DSN. 매 테스트마다 초기화된다.

    테이블을 하나씩 지우는 대신 스키마를 통째로 갈아 끼운다. 격리 규칙이
    "이 DB에는 직전 테스트의 흔적이 없다" 한 줄로 끝나고, 빈 스키마라 비용도 없다.
    """
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")

    return test_dsn
