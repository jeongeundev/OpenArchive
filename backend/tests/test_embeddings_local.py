"""BGE-M3 프로바이더의 계약 (ADR-003).

**실제 모델을 로드하는 테스트는 여기에 없다.** BGE-M3는 약 2GB이고 첫 로딩에 수십
초가 걸린다 — `scripts/check.sh`가 매 실행마다 그 비용을 치르게 된다. 실제 추론
확인은 `EMBEDDING_PROVIDER=local`로 워커를 한 번 돌려보는 수동 검증이다.

그래서 여기서 고정하는 것은 **모델 없이 확인할 수 있는 것들**이다: 계약값,
lazy-load, 그리고 `.[local]`을 설치하지 않은 사람이 받게 될 에러 메시지.
"""

import sys

import pytest

from app.embeddings.base import EMBEDDING_DIM
from app.embeddings.local import LocalProvider


def test_provider_declares_the_model_and_dimension():
    provider = LocalProvider()

    assert provider.name == "BAAI/bge-m3"
    assert provider.dimension == EMBEDDING_DIM == 1024


def test_creating_the_provider_does_not_load_the_model():
    """`get_provider()`가 워커 기동 시점에 호출된다 — 생성자에서 로드하면 기동이 막힌다."""
    assert LocalProvider()._model is None


def test_embed_reports_how_to_install_when_sentence_transformers_is_missing(monkeypatch):
    """`sys.modules`에 None을 넣으면 import가 실패한다 — 실제 설치 여부와 무관하게 재현된다.

    맨 ImportError를 흘리면 "왜 안 되는지"는 알아도 "어떻게 고치는지"는 모른다.
    기본 의존성이 아닌 것을 optional extra로 뺀 결정(ADR-003)의 비용을 여기서 갚는다.
    """
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(RuntimeError) as excinfo:
        LocalProvider().embed(["문서 본문"])

    message = str(excinfo.value)
    assert "sentence-transformers" in message
    assert 'pip install -e ".[dev,local]"' in message
