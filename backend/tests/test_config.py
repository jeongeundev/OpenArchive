from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ENV_FILE, Settings, get_settings

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

    assert settings.database_url == "postgresql://openarchive:openarchive@localhost:5433/openarchive"


def test_embedding_provider_defaults_to_fake(monkeypatch):
    """기본값은 모델을 내려받지 않는 fake다 — 테스트·CI가 2GB 모델에 묶이지 않게 한다."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    settings = Settings(**NO_ENV_FILE)

    assert settings.embedding_provider == "fake"


def test_session_cookie_secure_follows_the_deployment_setting(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    settings = Settings(**NO_ENV_FILE)

    assert settings.session_cookie_secure is True


def test_unknown_embedding_provider_is_rejected(monkeypatch):
    """상용 API 프로바이더는 대회 규정상 쓸 수 없다 — 설정 단계에서 막는다 (ADR-003)."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached():
    assert get_settings() is get_settings()


def test_env_file_is_read_from_the_backend_package_not_the_cwd(tmp_path, monkeypatch):
    """설정 파일 위치는 실행 디렉토리에 좌우되지 않는다.

    API·워커는 `backend/`에서 실행되고 `scripts/create_admin.py`는 저장소 루트에서
    실행된다. env_file이 cwd 상대 경로이면 **같은 .env 하나가 프로세스마다 다르게
    해석돼**, 계정은 이쪽 DB에 문서는 저쪽 DB에 쌓이는 상태가 에러 없이 만들어진다.
    """
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:59999/sentinel\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    assert "sentinel" not in settings.database_url
    # 무엇을 읽지 '않는지'만 단언하면 env_file=None으로 바꿔도 통과한다.
    # 읽는 대상이 backend/.env로 고정됐다는 것까지 함께 고정한다.
    assert Path(Settings.model_config["env_file"]) == ENV_FILE
    assert ENV_FILE.is_absolute()
    assert ENV_FILE.parent.name == "backend"


def test_env_file_values_are_applied_from_the_backend_package(tmp_path, monkeypatch):
    """고정된 위치의 .env를 실제로 읽는다 — 위 테스트의 반대 방향."""
    env_file = tmp_path / "backend" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(
        "DATABASE_URL=postgresql://fromfile:fromfile@127.0.0.1:5433/fromfile\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.database_url == "postgresql://fromfile:fromfile@127.0.0.1:5433/fromfile"
