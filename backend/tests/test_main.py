import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.embeddings import FakeProvider
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


class _RecordingProvider:
    """embed 호출을 세는 프로바이더 — 예열은 호출로만 관측된다.

    FakeProvider는 로딩이 없어 예열해도 겉으로 아무 일도 일어나지 않는다. 그래서
    지연이 아니라 호출을 센다.
    """

    name = "recording"
    dimension = 1024

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return FakeProvider().embed(texts)


def test_startup_warms_up_the_embedding_provider(monkeypatch, clean_db: str):
    """기동 시 임베딩 모델을 예열한다 — 검색 요청이 한 건도 없어도.

    lifespan은 프로바이더 **인스턴스**만 만들고, `LocalProvider`는 첫 `embed()`까지
    모델(~2GB) 로딩을 미룬다. 예열이 없으면 그 로딩이 통째로 **첫 검색 요청**에 붙는다 —
    2026-08-21 실측으로 갓 기동한 API의 첫 검색이 12.5초, 두 번째가 0.34초였다.
    워커의 예열과 같은 결함이지만 이쪽이 사용자에게 더 가깝다: 백그라운드 처리 지연이
    아니라, 사용자가 검색 버튼을 누르고 화면 앞에서 기다리는 시간이다.

    요청을 보내지 않고 확인하는 이유: 검색을 한 번이라도 태우면 그 요청의 embed와
    구분되지 않아 예열이 있었는지 증명할 수 없다.
    """
    monkeypatch.setenv("DATABASE_URL", clean_db)
    get_settings.cache_clear()
    provider = _RecordingProvider()
    monkeypatch.setattr("app.main.get_provider", lambda: provider)

    with TestClient(app):
        pass  # 기동과 종료만 — 요청은 보내지 않는다

    assert provider.calls == 1
