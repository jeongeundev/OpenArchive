"""임베딩 프로바이더 선택 (ARCHITECTURE.md "임베딩 프로바이더").

질의 임베딩도 문서 임베딩과 같은 프로바이더를 써야 한다 — 벡터 공간이 다르면 검색이
에러 없이 무의미해진다. 그래서 선택 지점을 이 함수 하나로 모은다.
"""

from app.config import get_settings
from app.embeddings.base import EMBEDDING_DIM, EmbeddingProvider
from app.embeddings.fake import FakeProvider
from app.embeddings.local import LocalProvider

__all__ = ["EMBEDDING_DIM", "EmbeddingProvider", "FakeProvider", "LocalProvider", "get_provider"]


def get_provider(name: str | None = None) -> EmbeddingProvider:
    """이름으로 프로바이더를 만든다. 이름이 없으면 `EMBEDDING_PROVIDER` 설정을 따른다.

    인스턴스를 캐시하지 않는다. `LocalProvider`가 모델을 첫 `embed()`까지 미루므로
    생성 비용이 사실상 0이고, 워커·API는 어차피 프로바이더를 한 번 만들어 재사용한다.
    캐시를 두면 초기화 수단까지 딸려 오는데, 그만한 값어치가 없다.
    """
    resolved = name if name is not None else get_settings().embedding_provider

    match resolved:
        case "fake":
            return FakeProvider()
        case "local":
            return LocalProvider()
        case _:
            # 상용 API 프로바이더는 존재하지 않는다 — 대회 규정상 만들 수 없다 (ADR-003).
            raise ValueError(f"알 수 없는 임베딩 프로바이더입니다: {resolved!r} (local | fake)")
