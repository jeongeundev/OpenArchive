"""검색 평가셋 지표(Recall@k·MRR)의 계산 규칙을 고정한다 (#94).

관계 판정·`ask`·sparse 채널을 바꿀 때마다 "좋아졌는가"를 이 수치로 말하므로,
순위를 세는 규칙(문서 단위 dedupe·정답 복수·미등재 제목 거부)이 흔들리면 전후
비교 자체가 무의미해진다.
"""

import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.eval_search import (
    QueryResult,
    rank_documents,
    recall_at_k,
    reciprocal_rank,
    resolve_relevant,
    summarize,
)

D1, D2, D3, D4 = (UUID(int=n) for n in range(1, 5))


def test_rank_documents_keeps_first_occurrence_order_and_drops_repeats():
    """검색 결과는 직접 히트가 앞, 관계 확장이 뒤다. 같은 문서가 두 번 오면 앞 순위만 센다."""
    assert rank_documents([D1, D2, D1, D3, D2]) == [D1, D2, D3]


def test_recall_at_k_counts_relevant_documents_inside_the_cutoff():
    ranked = [D1, D2, D3, D4]

    assert recall_at_k(ranked, {D1, D3}, 1) == 0.5
    assert recall_at_k(ranked, {D1, D3}, 3) == 1.0
    assert recall_at_k(ranked, {D4}, 3) == 0.0


def test_reciprocal_rank_uses_the_first_relevant_document_only():
    assert reciprocal_rank([D2, D1, D3], {D1, D3}) == 0.5
    assert reciprocal_rank([D2, D4], {D1}) == 0.0


def test_resolve_relevant_maps_titles_to_every_document_with_that_title():
    """제목은 유일 키가 아니다(ADR-030). 동명 문서가 둘이면 둘 다 정답이다."""
    title_to_ids = {"보안": [D1, D2], "홈": [D3]}

    assert resolve_relevant(["보안", "홈"], title_to_ids) == {D1, D2, D3}


def test_resolve_relevant_rejects_titles_missing_from_the_corpus():
    """평가셋 오타가 조용히 Recall 0으로 잡히면 변경 전후 비교가 틀어진다."""
    with pytest.raises(KeyError, match="없는 제목"):
        resolve_relevant(["없는 문서"], {"홈": [D3]})


def test_summarize_averages_per_query_metrics_and_counts_hits():
    results = [
        QueryResult(query="q1", ranked=[D1, D2], relevant={D1}, via_hits={D2}),
        QueryResult(query="q2", ranked=[D3, D1], relevant={D1, D4}, via_hits=set()),
        QueryResult(query="q3", ranked=[D3], relevant={D4}, via_hits=set()),
    ]

    summary = summarize(results, ks=(1, 2))

    assert summary["queries"] == 3
    assert summary["recall@1"] == pytest.approx((1.0 + 0.0 + 0.0) / 3)
    assert summary["recall@2"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert summary["mrr"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert summary["top1_hits"] == 1
    assert summary["misses"] == 1


def test_query_result_reports_whether_a_relevant_document_came_only_via_graph():
    """관계 확장(via)으로만 잡힌 정답은 직접 검색이 놓친 것이다 — 따로 센다."""
    result = QueryResult(query="q", ranked=[D2, D1], relevant={D1}, via_hits={D1})

    assert result.relevant_only_via == {D1}


def test_query_result_first_relevant_rank_is_one_based_or_none():
    assert QueryResult("q", [D2, D1], {D1}, set()).first_relevant_rank == 2
    assert QueryResult("q", [D2], {D1}, set()).first_relevant_rank is None


def _fresh_ids(n: int) -> list[UUID]:
    return [uuid4() for _ in range(n)]


def test_recall_is_capped_by_the_number_of_relevant_documents():
    """정답이 3개인데 k=1이면 최대 1/3이다 — 상위 1건이 정답이어도 1.0이 되지 않는다."""
    relevant = _fresh_ids(3)

    assert recall_at_k(relevant, set(relevant), 1) == pytest.approx(1 / 3)


@pytest.mark.parametrize("corpus", ["a", "c"])
def test_committed_evalsets_are_well_formed(corpus: str):
    """평가셋은 저장소 안에 있어야 다음 변경을 같은 잣대로 잰다 — 형식이 깨지면 스크립트가 아니라 여기서 멈춘다."""
    import json

    evalset = json.loads((ROOT / "scripts" / "eval" / f"{corpus}.json").read_text())

    assert evalset["corpus"] == corpus
    queries = evalset["queries"]
    assert len(queries) >= 20  # #94: 질의 20~30개
    assert all(item["query"].strip() and item["relevant"] for item in queries)
    assert all(title.strip() for item in queries for title in item["relevant"])
