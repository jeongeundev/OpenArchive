import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings

# 개발자 로컬에 .env가 있어도 기본값 검증이 흔들리지 않도록 _env_file=None으로 끊는다.
NO_ENV_FILE = {"_env_file": None}


def test_settings_are_injected_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@openproxy.example:6432/pool_a")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")

    settings = Settings()

    assert settings.database_url == "postgresql://app@openproxy.example:6432/pool_a"
    assert settings.embedding_provider == "local"


def test_database_url_defaults_to_local_compose_dsn(monkeypatch):
    """clone 직후 .env 없이도 로컬 컨테이너에 붙는다. docker-compose.yml의 자격증명과 같다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(**NO_ENV_FILE)

    assert settings.database_url == "postgresql://openarchive:openarchive@localhost:5432/openarchive"


def test_embedding_provider_defaults_to_fake(monkeypatch):
    """기본값은 모델을 내려받지 않는 fake다 — 테스트·CI가 2GB 모델에 묶이지 않게 한다."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    settings = Settings(**NO_ENV_FILE)

    assert settings.embedding_provider == "fake"


def test_unknown_embedding_provider_is_rejected(monkeypatch):
    """상용 API 프로바이더는 대회 규정상 쓸 수 없다 — 설정 단계에서 막는다 (ADR-003)."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
