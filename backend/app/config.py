from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# `.env`는 이 패키지 옆(`backend/.env`)에 두고, 실행 디렉토리와 무관하게 그 파일만 읽는다.
# cwd 상대 경로로 두면 **같은 .env 하나가 프로세스마다 다르게 해석된다** — API·워커는
# `backend/`에서, `scripts/create_admin.py`는 저장소 루트에서 실행되기 때문이다. 그러면
# 계정은 이쪽 DB에 문서는 저쪽 DB에 쌓이는 상태가 에러 없이 만들어진다.
# `scripts/deploy_app_host.sh`가 쓰는 위치도 여기다.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # 단일 엔드포인트 DSN. 실 클러스터에서는 OpenProxy VIP(:6432) 주소로 환경변수에서 덮어쓴다.
    # 멀티호스트 DSN이나 target_session_attrs를 여기에 두지 않는다 — 새 Primary 발견과
    # 재연결은 OpenProxy의 책임이다 (ADR-006).
    # 기본값은 docker-compose.yml의 로컬 컨테이너와 같다: clone 후 .env 없이도 기동된다.
    # 포트가 5433인 이유는 docker-compose.yml 주석 참조 — 호스트의 기존 PostgreSQL을 피한다.
    database_url: str = "postgresql://openarchive:openarchive@localhost:5433/openarchive"

    # local = BAAI/bge-m3, fake = 결정론적 해시 벡터(테스트·CI). 상용 API 프로바이더는 없다 (ADR-003).
    embedding_provider: Literal["local", "fake"] = "fake"

    # 0은 단일 워커 복구 데모 전용이다. 여러 워커에서 쓰면 정상 처리 중인 잡을 서로
    # 회수한다. 데모는 워커가 하나이고 run_worker의 sweep → drain 루프가 순차라 안전하다.
    zombie_timeout_minutes: int = 5

    # 한 근무일 동안 재로그인 없이 쓰되, 장기 토큰으로 남지 않도록 24시간으로 제한한다.
    session_lifetime_hours: int = 24

    # 로컬 HTTP에서는 Secure 쿠키가 전송되지 않는다. HTTPS 배포에서만 환경변수로 켠다.
    session_cookie_secure: bool = False

    # MCP는 HTTP 헤더가 없으므로 프로세스 환경으로만 사용자 컨텍스트를 고정한다.
    # 미설정(None)이면 서비스 권한 술어에 따라 public 문서만 보인다.
    mcp_user_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
