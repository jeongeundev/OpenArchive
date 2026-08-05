import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_health_returns_ok(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_runs_migrations(monkeypatch, clean_db: str):
    """마이그레이션 실행 주체는 API 서버 하나다 (ADR-012).

    워커·MCP 서버에는 이 경로가 없으므로, 여기서 돌지 않으면 아무도 돌리지 않는다.
    `client` 픽스처는 컨텍스트 매니저가 아니라 lifespan을 태우지 않는다 —
    lifespan을 보려면 이렇게 명시적으로 감싸야 한다.
    """
    monkeypatch.setenv("DATABASE_URL", clean_db)
    get_settings.cache_clear()

    with TestClient(app) as started:
        assert started.get("/api/health").json() == {"status": "ok"}

    with psycopg.connect(clean_db) as conn:
        (created,) = conn.execute(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        ).fetchone()

    assert created is True


def test_startup_fails_loudly_when_migrations_cannot_run(monkeypatch):
    """마이그레이션 실패를 삼키지 않는다.

    스키마가 준비되지 않은 채 API가 요청을 받으면 실패가 런타임으로 미뤄진다.
    부분 적용된 스키마 위에서 도는 것이 기동 실패보다 위험하다.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nowhere")
    get_settings.cache_clear()

    with pytest.raises(psycopg.OperationalError), TestClient(app):
        pass
