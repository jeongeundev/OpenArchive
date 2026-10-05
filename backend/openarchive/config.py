import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def openarchive_home() -> Path:
    """설정 디렉토리. `$OPENARCHIVE_HOME`, 없으면 `~/.openarchive`.

    설치 위치(패키지 옆)가 아니라 사용자 홈에 둔다 — 비편집 설치에서 패키지 옆은
    site-packages라 사용자가 손댈 자리가 아니고, 재설치하면 지워진다 (#90-1).
    """
    configured = os.environ.get("OPENARCHIVE_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".openarchive"


# 실행 디렉토리와 무관하게 이 파일 하나만 읽는다. cwd 상대 경로로 두면 **같은 .env 하나가
# 프로세스마다 다르게 해석된다** — `openarchive` CLI·API·워커·MCP 서버가 서로 다른
# 디렉토리에서 실행되기 때문이다. 그러면 계정은 이쪽 DB에 문서는 저쪽 DB에 쌓이는 상태가
# 에러 없이 만들어진다. `scripts/deploy_app_host.sh`가 쓰는 위치도 여기다.
ENV_FILE = openarchive_home() / ".env"


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

    # 기본 꺼짐으로 모델 설치를 선택으로 남긴다. 상용 API 프로바이더는 없다(ADR-003·043).
    answer_provider: Literal["off", "ollama", "fake"] = "off"

    # 로컬 Ollama 서버 주소. 모델 적재는 서버가 관리하며 앱 기동 시 접속하지 않는다.
    ollama_url: str = "http://localhost:11434"

    # 임시 모델 태그 — #96 c의 한국어 실측으로 확정한다 (ADR-043).
    answer_model: str = "qwen3:8b"

    # 생성 호출 한 번의 HTTP 타임아웃(초). 모델 실패와 DB 일시 불가용을 구분한다.
    answer_timeout_seconds: float = Field(default=120, gt=0)

    # 프롬프트에 넣는 근거 본문의 글자 예산. 생성 모델에 전달할 문맥량을 제한한다.
    answer_context_chars: int = Field(default=6000, gt=0)

    # 잡 선점 lease(초). 워커는 처리 중 이 값의 1/3마다 연장하고, 연장이 끊긴 잡은 lease
    # 만료 뒤 회수된다 (ADR-050). 스윕도 drain 중 이 주기로 돈다.
    job_lease_seconds: int = Field(default=60, gt=0)

    # 한 근무일 동안 재로그인 없이 쓰되, 장기 토큰으로 남지 않도록 24시간으로 제한한다.
    session_lifetime_hours: int = 24

    # 로컬 HTTP에서는 Secure 쿠키가 전송되지 않는다. HTTPS 배포에서만 환경변수로 켠다.
    session_cookie_secure: bool = False

    # 업로드 파일 상한(MB, 십진 — 1MB = 1,000,000바이트). 원본을 DB의 document_files에
    # 보관하므로(ADR-046) 이 값은 업로드 한 번이 늘릴 수 있는 DB 크기의 상한이기도 하다.
    # 50MB는 실 OpenSQL에서 OpenProxy 경유 statement_timeout 30s 아래로 통과했다
    # (업로드 9.1~9.6s, ADR-046 트레이드오프 4).
    max_upload_mb: int = 50

    # MCP는 HTTP 헤더가 없으므로 프로세스 환경으로만 사용자 컨텍스트를 고정한다.
    # 미설정(None)이면 서비스 권한 술어에 따라 public 문서만 보인다.
    mcp_user_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
