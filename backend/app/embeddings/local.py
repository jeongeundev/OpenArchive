"""BGE-M3 임베딩 프로바이더 — 운영 경로 (ADR-003).

`BAAI/bge-m3`는 MIT 라이선스에 1024차원, 로컬에서 직접 구동된다. 대회 규정 [별표2]가
요구하는 "독립 구동 가능성"을 충족하며, 상용 API 모델은 애초에 쓸 수 없다.

배칭·캐싱·폴백 체인·재시도는 만들지 않는다 (ARCHITECTURE.md 임베딩 프로바이더 절).
"""

from typing import Any

from app.embeddings.base import EMBEDDING_DIM

INSTALL_HINT = (
    "sentence-transformers가 설치되어 있지 않습니다. "
    "모델 의존성은 optional extra로 분리되어 있습니다 — backend/에서 실행하세요:\n"
    '  pip install -e ".[dev,local]"\n'
    "모델을 내려받지 않고 파이프라인만 확인하려면 EMBEDDING_PROVIDER=fake를 쓰세요."
)


class LocalProvider:
    name = "BAAI/bge-m3"
    dimension = EMBEDDING_DIM

    def __init__(self) -> None:
        # 여기서 로드하지 않는다. `get_provider()`는 워커 기동 시점에 호출되며,
        # 그때 2GB를 내려받으면 기동이 그만큼 막힌다. 첫 embed() 호출에 미룬다.
        self._model: Any = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        # normalize_embeddings=True — 코사인 거리(`<=>`)를 쓰는 전제다 (ADR-002).
        return self._load().encode(texts, normalize_embeddings=True).tolist()

    def _load(self) -> Any:
        if self._model is None:
            # 모듈 최상위에서 import하면 `.[local]`을 설치하지 않은 환경에서
            # `app.embeddings` import 자체가 깨진다 — 워커도 API도 기동하지 못한다.
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(INSTALL_HINT) from exc

            self._model = SentenceTransformer(self.name)
        return self._model
