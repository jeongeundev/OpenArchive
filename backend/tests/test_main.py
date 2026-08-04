from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_health_returns_ok(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_starts_without_a_reachable_database(monkeypatch):
    """기동 경로가 DB에 의존하지 않는다 (ADR-012).

    닿을 수 없는 DSN을 주입한 채 lifespan을 태운다. startup에서 풀을 열거나
    마이그레이션을 돌린다면 여기서 실패한다. 로컬 컨테이너가 떠 있든 아니든
    결과가 같아야 하므로, DSN을 일부러 접속 불가한 값으로 바꾼다.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nowhere")
    get_settings.cache_clear()

    with TestClient(app) as started:
        assert started.get("/api/health").json() == {"status": "ok"}
