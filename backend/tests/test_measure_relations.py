"""§14의 판정을 떠받치는 EXPLAIN 해석을 고정한다.

`OPENSQL_RESEARCH.md` §14는 "상관 LATERAL이 HNSW를 전혀 타지 않았다"를 근거로 176.1ms를
규모 확장 근거에서 제외했고, 008 트리거가 청크별 상수 프로브를 고른 이유도 여기서 나왔다.
그 판정 전부가 `plan_uses_hnsw` 하나에 걸려 있으므로 이 함수의 계약을 못박는다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure_relations import plan_uses_hnsw

# 벡터 정렬이 idx_chunks_embedding을 탄 계획. EXPLAIN (FORMAT JSON)의 실제 형태를 줄인 것이다.
HNSW_PLAN = {
    "Plan": {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "documents"},
            {
                "Node Type": "Index Scan",
                "Index Name": "idx_chunks_embedding",
                "Relation Name": "document_chunks",
                "Order By": "(embedding <=> '[...]'::vector)",
            },
        ],
    },
    "Execution Time": 2.92,
}

# §14가 실제로 만난 계획 — 상관 LATERAL이라 인덱스를 못 타고 매 행마다 정렬로 떨어진다.
SORT_FALLBACK_PLAN = {
    "Plan": {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "document_chunks"},
            {
                "Node Type": "Sort",
                "Sort Key": ["((c.embedding <=> me.embedding))"],
                "Plans": [{"Node Type": "Seq Scan", "Relation Name": "document_chunks"}],
            },
        ],
    },
    "Execution Time": 176.1,
}

# 인덱스는 탔지만 벡터 인덱스가 아닌 계획. 권한 필터가 인덱스를 타면 이 형태가 된다.
OTHER_INDEX_PLAN = {
    "Plan": {
        "Node Type": "Index Scan",
        "Index Name": "idx_documents_owner_id",
        "Relation Name": "documents",
    },
    "Execution Time": 0.83,
}


def test_plan_using_the_embedding_index_is_reported_as_hnsw():
    assert plan_uses_hnsw(HNSW_PLAN) is True


def test_correlated_lateral_falling_back_to_sort_is_not_reported_as_hnsw():
    """§14의 176.1ms가 규모 확장 근거에서 빠진 이유가 이 판정이다."""
    assert plan_uses_hnsw(SORT_FALLBACK_PLAN) is False


def test_index_scan_on_a_non_vector_index_is_not_reported_as_hnsw():
    """`Index Scan`이 보인다는 것만으로 벡터 정렬이 인덱스를 탔다고 읽으면 안 된다."""
    assert plan_uses_hnsw(OTHER_INDEX_PLAN) is False
