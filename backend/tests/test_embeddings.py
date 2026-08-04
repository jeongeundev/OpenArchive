"""임베딩 프로바이더의 계약 (ARCHITECTURE.md "임베딩 프로바이더", ADR-003).

여기서 확인하는 것은 벡터의 "품질"이 아니라 **후속 단계가 기대는 성질**이다.

1. 길이와 차원 — `vector(1024)` 컬럼과 한 쌍이다. 어긋나면 INSERT가 터진다
2. 결정론 — 재임베딩이 멱등하려면 필요하다 (청킹의 결정론과 짝을 이룬다)
3. **프로세스 간 안정성** — 워커가 만든 문서 벡터와 API가 만든 질의 벡터는 서로 다른
   프로세스에서 계산된다. 벡터 공간이 프로세스마다 달라지면 검색이 조용히 망가진다
4. L2 정규화 — 코사인 거리(`<=>`)를 쓰는 전제다 (ADR-002)
5. 어휘 유사성 — `FakeProvider`로 검색 phase를 테스트하려면 "관련된 텍스트가 더
   가깝다"가 성립해야 한다. 무작위 벡터로는 그 테스트를 쓸 수 없다
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.embeddings import get_provider
from app.embeddings.base import EMBEDDING_DIM
from app.embeddings.fake import FakeProvider
from app.embeddings.local import LocalProvider

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 어휘가 겹치는 A·B와, 둘 중 어느 쪽과도 단어를 공유하지 않는 C.
SHARED_A = "임베딩 잡은 트리거가 만들고 워커가 그것을 집어간다"
SHARED_B = "임베딩 잡은 트리거가 만든다"
DISJOINT_C = "회의실 예약은 금요일 오후 여섯시에 마감된다"

# 별도 프로세스에서 같은 텍스트를 임베딩한다. 내장 hash()도 함께 찍어,
# PYTHONHASHSEED가 실제로 먹히는 환경인지(=이 테스트가 의미 있는지) 확인한다.
PROBE = (
    "import json;"
    "from app.embeddings.fake import FakeProvider;"
    "print(json.dumps({"
    "  'vector': FakeProvider().embed(['정합성은 DB가 보장한다 opensql'])[0],"
    "  'builtin_hash': hash('정합성은 DB가 보장한다 opensql'),"
    "}))"
)


def embed_in_subprocess(hash_seed: str) -> dict:
    """`PYTHONHASHSEED`를 지정한 새 파이썬 프로세스에서 임베딩한다."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        # check=True로 던지면 stderr가 예외 메시지에 안 실려 실패 원인을 볼 수 없다.
        check=False,
    )
    assert result.returncode == 0, f"하위 프로세스 실패:\n{result.stderr}"
    return json.loads(result.stdout)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_returns_one_vector_per_text():
    vectors = FakeProvider().embed(["첫 번째 청크", "두 번째 청크", "세 번째 청크"])

    assert len(vectors) == 3


def test_fake_vectors_have_the_column_dimension():
    """1024는 `document_chunks.embedding vector(1024)`와 한 쌍이다 (ADR-003)."""
    (vector,) = FakeProvider().embed(["문서 본문"])

    assert EMBEDDING_DIM == 1024
    assert len(vector) == EMBEDDING_DIM
    assert FakeProvider().dimension == EMBEDDING_DIM


def test_fake_embedding_of_empty_list_is_empty():
    assert FakeProvider().embed([]) == []


def test_fake_is_deterministic():
    """같은 본문을 다시 임베딩하면 같은 벡터다 — 재임베딩 멱등성의 전제."""
    provider = FakeProvider()

    first = provider.embed([SHARED_A])
    second = FakeProvider().embed([SHARED_A])

    assert first == second


def test_fake_is_stable_across_processes():
    """내장 hash()는 프로세스마다 값이 달라진다. 그것을 쓰지 않았음을 강제한다.

    워커(문서 벡터)와 API(질의 벡터)는 다른 프로세스다. 여기서 어긋나면 에러 없이
    검색 결과만 무의미해진다 — 가장 발견하기 어려운 종류의 고장이다.
    """
    one = embed_in_subprocess("1")
    other = embed_in_subprocess("2")

    # 이 테스트가 실제로 무언가를 검증하고 있는지부터 확인한다.
    assert one["builtin_hash"] != other["builtin_hash"], (
        "PYTHONHASHSEED가 먹지 않는 환경이다 — 이 테스트는 hash() 사용을 잡아내지 못한다"
    )
    assert one["vector"] == other["vector"]


def test_fake_vectors_are_l2_normalized():
    """코사인 거리를 쓰므로 노름이 1이어야 실제 임베딩과 같은 성질을 갖는다 (ADR-002)."""
    vectors = FakeProvider().embed([SHARED_A, SHARED_B, DISJOINT_C])

    for vector in vectors:
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)


def test_fake_embedding_without_tokens_is_a_zero_vector():
    """토큰이 없으면 정규화할 것이 없다. 0으로 나누지 않고 0 벡터를 낸다."""
    (vector,) = FakeProvider().embed(["   \t\n  "])

    assert len(vector) == EMBEDDING_DIM
    assert not any(vector)


def test_fake_places_texts_sharing_vocabulary_closer():
    """검색 phase가 `FakeProvider`로 "관련 문서가 상위에 온다"를 검증할 수 있어야 한다."""
    shared_a, shared_b, disjoint_c = FakeProvider().embed([SHARED_A, SHARED_B, DISJOINT_C])

    assert cosine(shared_a, shared_b) > cosine(shared_a, disjoint_c)
    assert cosine(shared_a, shared_b) > cosine(shared_b, disjoint_c)


def test_get_provider_returns_the_named_provider():
    assert isinstance(get_provider("fake"), FakeProvider)
    assert isinstance(get_provider("local"), LocalProvider)


def test_get_provider_rejects_unknown_names():
    """상용 API 프로바이더는 존재하지 않는다 — 대회 규정상 만들 수 없다 (ADR-003)."""
    with pytest.raises(ValueError, match="openai"):
        get_provider("openai")


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("fake", FakeProvider), ("local", LocalProvider)],
)
def test_get_provider_follows_the_configured_provider(monkeypatch, configured, expected):
    monkeypatch.setenv("EMBEDDING_PROVIDER", configured)

    assert isinstance(get_provider(), expected)


def test_get_provider_does_not_load_the_model_for_local():
    """워커 기동 시점에 호출된다. 여기서 2GB를 로드하면 기동이 막힌다."""
    provider = get_provider("local")

    assert provider._model is None
