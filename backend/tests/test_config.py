import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openarchive.config import ENV_FILE, Settings, get_settings, openarchive_home

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


@pytest.mark.parametrize("value", ["0", "-5"])
def test_job_lease_must_be_positive(monkeypatch, value):
    """lease가 0 이하면 heartbeat가 쉬지 않고 돌고, 방금 선점한 잡도 즉시 좀비가 된다 (ADR-050).

    옛 `ZOMBIE_TIMEOUT_MINUTES=0`(단일 워커 복구 데모)에 해당하는 값은 없다 — lease는
    heartbeat가 지키므로 짧게 둬도 정상 잡을 회수하지 않는다.
    """
    monkeypatch.setenv("JOB_LEASE_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(**NO_ENV_FILE)


def test_get_settings_is_cached():
    assert get_settings() is get_settings()


def test_trash_retention_defaults_to_30_days(monkeypatch):
    monkeypatch.delenv("TRASH_RETENTION_DAYS", raising=False)
    assert Settings(**NO_ENV_FILE).trash_retention_days == 30


def test_trash_retention_is_injected_from_environment(monkeypatch):
    monkeypatch.setenv("TRASH_RETENTION_DAYS", "7")
    assert Settings(**NO_ENV_FILE).trash_retention_days == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_trash_retention_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("TRASH_RETENTION_DAYS", value)
    with pytest.raises(ValidationError):
        Settings(**NO_ENV_FILE)


def test_env_file_is_read_from_openarchive_home_not_the_cwd(tmp_path, monkeypatch):
    """설정 파일 위치는 실행 디렉토리에 좌우되지 않는다.

    `openarchive serve`·워커·MCP 서버·`openarchive` CLI가 서로 다른 디렉토리에서 실행된다.
    env_file이 cwd 상대 경로이면 **같은 .env 하나가 프로세스마다 다르게 해석돼**, 계정은
    이쪽 DB에 문서는 저쪽 DB에 쌓이는 상태가 에러 없이 만들어진다.
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
    assert Path(Settings.model_config["env_file"]) == ENV_FILE
    assert ENV_FILE.is_absolute()


def test_openarchive_home_defaults_to_a_directory_in_the_user_home(monkeypatch):
    """설치 위치(site-packages)가 아니라 사용자 홈에 둔다 — 비편집 설치의 패키지 옆은
    사용자가 손댈 자리가 아니고, 재설치하면 지워진다 (#90-1)."""
    monkeypatch.delenv("OPENARCHIVE_HOME", raising=False)

    assert openarchive_home() == Path.home() / ".openarchive"


def test_openarchive_home_can_be_overridden_by_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENARCHIVE_HOME", "relative-home")

    # 상대 경로는 절대 경로로 굳힌다. 굳히지 않으면 위 cwd 문제가 이 변수로 되살아난다.
    assert openarchive_home() == (tmp_path / "relative-home").resolve()


def test_a_process_reads_the_env_file_in_openarchive_home(tmp_path):
    """규칙 전체를 실제 프로세스로 확인한다 — ENV_FILE은 import 시점에 정해진다."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "DATABASE_URL=postgresql://fromfile:fromfile@127.0.0.1:5433/fromfile\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["OPENARCHIVE_HOME"] = str(home)

    result = subprocess.run(
        [
            sys.executable, "-c",
            "from openarchive.config import get_settings; print(get_settings().database_url)",
        ],
        cwd=elsewhere,
        env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "postgresql://fromfile:fromfile@127.0.0.1:5433/fromfile"


def test_answer_settings_defaults(monkeypatch):
    for name in ("ANSWER_PROVIDER", "OLLAMA_URL", "ANSWER_MODEL",
                 "ANSWER_TIMEOUT_SECONDS", "ANSWER_CONTEXT_CHARS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(**NO_ENV_FILE)
    assert settings.answer_provider == "off"
    assert settings.ollama_url == "http://localhost:11434"
    assert settings.answer_model == "qwen3:8b"
    assert settings.answer_timeout_seconds == 120
    assert settings.answer_context_chars == 6000


@pytest.mark.parametrize("name,value", [
    ("ANSWER_PROVIDER", "gpt"),
    ("ANSWER_TIMEOUT_SECONDS", "0"),
    ("ANSWER_CONTEXT_CHARS", "0"),
])
def test_invalid_answer_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(**NO_ENV_FILE)
