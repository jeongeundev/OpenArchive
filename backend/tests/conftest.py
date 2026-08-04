import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


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
    return TestClient(app)
