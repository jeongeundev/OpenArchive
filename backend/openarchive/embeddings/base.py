"""임베딩 프로바이더의 계약 (ARCHITECTURE.md "임베딩 프로바이더").

운영 경로는 `LocalProvider`(BGE-M3) 하나뿐이다. 상용 API 기반 프로바이더는 만들지
않는다 — 대회 규정 [별표2]가 "외부 API 호출을 통해서만 작동하는 API 전용 모델"을
금지한다 (ADR-003). `FakeProvider`는 테스트 전용이다.

프로바이더가 둘뿐이라 추상 기반 클래스도 레지스트리도 두지 않는다. Protocol 하나가
확장 지점을 드러내는 것으로 충분하다.
"""

from typing import Protocol

# `document_chunks.embedding vector(1024)` 컬럼과 한 쌍이다. BGE-M3의 출력 차원이며,
# 프로바이더가 바뀌어도 이 값은 바꾸지 않는다 (ADR-003). 설정으로 노출하지도 않는다 —
# 컬럼 타입이 SQL에 박혀 있어 런타임에 달라질 수 있는 값이 아니다.
EMBEDDING_DIM = 1024


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """텍스트마다 정규화된 `EMBEDDING_DIM`차원 벡터를 하나씩 반환한다.

        입력과 출력의 길이·순서가 같다. 코사인 거리(`<=>`)를 쓰므로 노름은 1이다
        (ADR-002).
        """
        ...
