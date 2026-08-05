"""테스트용 결정론적 임베딩 (ADR-003).

편의 장치가 아니라 **이 프로젝트 TDD의 핵심 인프라**다. BGE-M3는 약 2GB이고 첫
로딩에 수십 초가 걸린다 — 워커·검색 테스트를 매번 그 위에서 돌릴 수는 없다. 파이프라인
전체를 CI 속도로 검증하려면 모델 없이 같은 성질을 내는 대역이 필요하다.

"같은 성질"이 무엇인지가 이 파일의 설계 전부다.

- **프로세스 간 안정성**: 문서 벡터는 워커가, 질의 벡터는 API가 만든다. 서로 다른
  프로세스이므로 내장 `hash()`를 쓰면 벡터 공간이 프로세스마다 달라진다
  (`PYTHONHASHSEED`가 실행마다 무작위다). 그래서 `hashlib`을 쓴다
- **어휘 유사성**: 토큰마다 차원을 배정해 누적한다(feature hashing). 텍스트 전체를 한 번
  해싱하면 모든 벡터가 무작위로 흩어져, 검색 phase의 "관련 문서가 상위에 온다"를
  검증할 수 없게 된다
- **L2 정규화**: 코사인 거리(`<=>`)를 쓰는 전제 (ADR-002)

의미를 담지는 않는다. 어휘가 겹치는 만큼만 가까워질 뿐이며, 그것이 파이프라인 검증에
필요한 전부다.
"""

import hashlib
import math

from app.embeddings.base import EMBEDDING_DIM


class FakeProvider:
    name = "fake"
    dimension = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]


def _hash_vector(text: str) -> list[float]:
    """공백으로 나눈 토큰마다 차원 하나에 ±1을 더하고 L2 정규화한다."""
    vector = [0.0] * EMBEDDING_DIM
    for token in text.split():
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        # 부호를 섞지 않으면 모든 벡터가 1사분면에 몰려 유사도가 전부 양수로 뭉친다.
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        # 토큰이 없는 입력(공백뿐인 텍스트). 0으로 나누지 않고 0 벡터를 그대로 낸다.
        return vector
    return [x / norm for x in vector]
