from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 단일 엔드포인트 DSN. 실 클러스터에서는 OpenProxy VIP(:6432) 주소로 환경변수에서 덮어쓴다.
    # 멀티호스트 DSN이나 target_session_attrs를 여기에 두지 않는다 — 새 Primary 발견과
    # 재연결은 OpenProxy의 책임이다 (ADR-006).
    # 기본값은 docker-compose.yml의 로컬 컨테이너와 같다: clone 후 .env 없이도 기동된다.
    # 포트가 5433인 이유는 docker-compose.yml 주석 참조 — 호스트의 기존 PostgreSQL을 피한다.
    database_url: str = "postgresql://openarchive:openarchive@localhost:5433/openarchive"

    # local = BAAI/bge-m3, fake = 결정론적 해시 벡터(테스트·CI). 상용 API 프로바이더는 없다 (ADR-003).
    embedding_provider: Literal["local", "fake"] = "fake"


@lru_cache
def get_settings() -> Settings:
    return Settings()
