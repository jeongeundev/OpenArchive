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


def _hit(title, *, content="설정 안내", preview=None, passages=(), via=None):
    from openarchive.services.search import SearchHit

    return SearchHit(
        document_id=D1, title=title, filename=None, tags=[], content_type="md",
        chunk_index=0, content=content, score=0.8, based_on_version=1,
        via=via, preview=preview, passages=passages,
    )


def test_evidence_separates_document_hit_from_first_preview_and_candidate_coverage():
    from scripts.eval_search import grade_evidence

    from openarchive.services.search import SearchPassage

    evidence = [{"source": "운영", "text": "알림이 유실돼도 폴링이 처리한다"}]
    hit = _hit("운영", passages=(SearchPassage(8, evidence[0]["text"], 1, 0.6),))
    result = grade_evidence([hit], evidence)
    assert not result["first_excerpt"]["complete"]
    assert not result["first_preview"]["complete"]
    assert result["top5_passages"]["complete"]
    assert result["top5_passages"]["matched"] == result["top5_passages"]["required"] == 1
    assert result["first_preview"]["missing"] == evidence


def test_evidence_requires_all_fragments_in_the_named_source_without_joining_passages():
    from scripts.eval_search import grade_evidence

    from openarchive.services.search import SearchPassage

    evidence = [{"source": "운영", "text": "원본은 보관된다"}, {"source": "운영", "text": "용량은 누적된다"}]
    wrong = _hit("다른 문서", content="원본은 보관된다. 용량은 누적된다.")
    partial = _hit("운영", preview="원본은\n  보관된다", passages=(
        SearchPassage(0, "원본은 보관된다", 1, 0.8),
        SearchPassage(2, "용량은 누적된다", 1, 0.7),
    ))
    result = grade_evidence([wrong, partial], evidence)
    assert not result["first_excerpt"]["complete"]
    assert result["top5_passages"]["complete"]
    assert not grade_evidence([partial], [{"source": "운영", "text": "보관된다 용량은"}])["top5_passages"]["complete"]


def test_evidence_excludes_revision_and_graph_results_and_reports_empty_search_as_failure():
    from scripts.eval_search import grade_evidence

    from openarchive.services.search import SearchVia

    evidence = [{"source": "운영", "text": "예전 동작"}]
    revision = _hit("운영", content="예전 동작", preview="예전 동작", via=SearchVia(D2, "revision", 1))
    for hits in [[], [revision]]:
        result = grade_evidence(hits, evidence)
        assert all(not metric["complete"] and metric["matched"] == 0 for metric in result.values())


def test_evidence_normalizes_whitespace_but_does_not_grade_semantic_paraphrases():
    from scripts.eval_search import grade_evidence

    evidence = [{"source": "운영", "text": "이전 버전으로 검색한다"}]
    exact = grade_evidence([_hit("운영", preview="  이전\n 버전으로\t검색한다  ")], evidence)
    assert exact["first_preview"]["complete"]
    paraphrase = grade_evidence([_hit("운영", preview="옛 청크를 사용한다")], evidence)
    assert not paraphrase["first_preview"]["complete"]
    assert grade_evidence([], None) is None
    with pytest.raises(ValueError, match="근거"):
        grade_evidence([], [{"source": "운영", "text": "   "}])
    with pytest.raises(ValueError, match="근거"):
        grade_evidence([], [])


def test_summary_counts_only_queries_with_fixed_evidence():
    from scripts.eval_search import grade_evidence

    evidence = [{"source": "운영", "text": "정답 대목"}]
    good = grade_evidence([_hit("운영", content="정답 대목", preview="정답 대목")], evidence)
    bad = grade_evidence([_hit("운영")], evidence)
    results = [QueryResult("good", [D1], {D1}, set(), evidence=good),
               QueryResult("bad", [D1], {D1}, set(), evidence=bad),
               QueryResult("legacy", [D1], {D1}, set())]
    summary = summarize(results)
    assert summary["top1_hits"] == 3
    assert summary["evidence_queries"] == 2
    assert summary["first_preview_complete"] == 1
    assert summary["top5_passages_complete"] == 0


async def test_evaluator_resolves_and_searches_the_same_private_tagged_scope(migrated_db, monkeypatch):
    import psycopg
    from conftest import insert_test_document, process_all_embedding_jobs
    from scripts import eval_search

    from openarchive.embeddings import FakeProvider

    provider = FakeProvider()
    monkeypatch.setattr(eval_search, "get_provider", lambda: provider)
    text = "알림이 유실돼도 다음 폴링이 처리한다."
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        allowed = await insert_test_document(conn, title="운영", content=text,
                                             owner_id="alice", visibility="private", tags=["evaluation"])
        await insert_test_document(conn, title="운영", content="숨김 문서",
                                   owner_id="bob", visibility="private", tags=["evaluation"])
        await insert_test_document(conn, title="운영", content="다른 태그", tags=["other"])
        await process_all_embedding_jobs(conn, provider)
    evalset = {"queries": [{"query": "알림이 유실돼도 다음 폴링이 처리한다", "relevant": ["운영"],
                            "evidence": [{"source": "운영", "text": text}]}]}
    results = await eval_search.evaluate(evalset, migrated_db, user_id="alice", tags=["evaluation"])
    assert results[0].relevant == {allowed}
    assert results[0].ranked == [allowed]
    assert results[0].evidence["first_preview"]["complete"]
    assert results[0].evidence["top5_passages"]["complete"]
    assert results[0].preview_diagnosis["reason"] == "complete"
    with pytest.raises(KeyError, match="없는 제목"):
        await eval_search.evaluate(evalset, migrated_db, user_id="carol", tags=["evaluation"])


def test_render_exposes_body_failure_even_when_relevant_document_is_first():
    from scripts.eval_search import grade_evidence, render

    evidence = [{"source": "운영", "text": "답 대목"}]
    result = QueryResult("실패 사례", [D1], {D1}, set(), ["운영"], grade_evidence([_hit("운영")], evidence))
    output = render([result], summarize([result]))
    assert " 1  실패 사례" in output
    assert "발췌=0 미리보기=0 후보=0" in output
    legacy = QueryResult("기존 사례", [D1], {D1}, set(), ["운영"])
    assert "근거:" not in render([legacy], summarize([legacy]))


def test_minimum_evidence_window_counts_raw_whitespace_and_overlapping_occurrences():
    from scripts.eval_search import minimum_evidence_window

    evidence = [{"source": "운영", "text": "a a"}, {"source": "운영", "text": "a b"}]
    assert minimum_evidence_window("운영", "a a a b", evidence) == 5
    evidence = [{"source": "운영", "text": "앞 답"}, {"source": "운영", "text": "뒤 답"}]
    assert minimum_evidence_window("운영", "앞\n\t답 x 뒤 답", evidence) == 10
    assert minimum_evidence_window("다른 문서", "앞 답 뒤 답", evidence) is None
    assert minimum_evidence_window("운영", "앞 답만 존재", evidence) is None


def test_preview_diagnosis_separates_selection_scope_and_length():
    from dataclasses import replace

    from scripts.eval_search import diagnose_preview

    from openarchive.services.search import SearchPassage

    evidence = [{"source": "운영", "text": "앞"}, {"source": "운영", "text": "뒤"}]
    answer = "앞" + "x" * 298 + "뒤"
    hit = _hit("운영", content=answer, preview="잘못 선택", passages=(SearchPassage(2, answer, 1, .8),))
    assert diagnose_preview([hit], evidence) == {
        "reason": "selection", "first_excerpt_minimum_chars": 300,
        "first_document_passage_minimum_chars": 300,
    }
    hit = replace(hit, preview=answer)
    assert diagnose_preview([hit], evidence)["reason"] == "complete"
    hit = replace(hit, preview="잘못 선택", content="다른 내용")
    assert diagnose_preview([hit], evidence)["reason"] == "scope"
    hit = replace(hit, passages=(SearchPassage(2, "앞" + "x" * 299 + "뒤", 1, .8),))
    assert diagnose_preview([hit], evidence)["reason"] == "length"


def test_preview_diagnosis_does_not_join_passages_or_use_another_document():
    from dataclasses import replace

    from scripts.eval_search import diagnose_preview

    from openarchive.services.search import SearchPassage, SearchVia

    evidence = [{"source": "운영", "text": "앞"}, {"source": "운영", "text": "뒤"}]
    partial = _hit("운영", passages=(SearchPassage(1, "앞", 1, .8), SearchPassage(8, "뒤", 1, .7)))
    good = _hit("운영", content="앞 뒤", preview="앞 뒤", passages=(SearchPassage(1, "앞 뒤", 1, .6),))
    assert diagnose_preview([partial, good], evidence)["reason"] == "missing"
    assert diagnose_preview([], evidence)["reason"] == "missing"
    good = replace(good, via=SearchVia(D2, "revision", 1))
    assert diagnose_preview([good], evidence)["reason"] == "missing"
    assert diagnose_preview([], None) is None


def test_render_exposes_preview_diagnosis_and_keeps_legacy_output():
    from scripts.eval_search import diagnose_preview, render

    hit = _hit("운영", content="정답 대목", preview="다른 내용")
    result = QueryResult("선택 실패", [D1], {D1}, set(), ["운영"],
                         preview_diagnosis=diagnose_preview([hit], [{"source": "운영", "text": "정답 대목"}]))
    assert "미리보기 원인=selection" in render([result], summarize([result]))
    legacy = QueryResult("기존 사례", [D1], {D1}, set(), ["운영"])
    assert "미리보기 원인=" not in render([legacy], summarize([legacy]))


def test_failure_cause_separates_extraction_candidates_and_selection():
    """#167: 근거가 추출 본문에 없으면 검색을 탓할 수 없다 — 원인을 추출·후보 부족·선택으로 나눈다."""
    from scripts.eval_search import classify_failure, grade_evidence

    from openarchive.services.search import SearchPassage

    evidence = [{"source": "운영", "text": "정답 대목"}]
    stored = {"운영": ["앞 내용. 정답 대목. 뒤 내용."]}
    complete = [_hit("운영", content="정답 대목")]
    selection = [_hit("운영", content="앞 내용", passages=(SearchPassage(1, "정답 대목", 1, .7),))]
    candidates = [_hit("운영", content="앞 내용")]
    assert classify_failure(evidence, stored, grade_evidence(complete, evidence)) == "complete"
    assert classify_failure(evidence, stored, grade_evidence(selection, evidence)) == "selection"
    assert classify_failure(evidence, stored, grade_evidence(candidates, evidence)) == "candidates"
    misread = {"운영": ["앞 내용. 정답 대룩. 뒤 내용."]}
    assert classify_failure(evidence, misread, grade_evidence(candidates, evidence)) == "extraction"
    assert classify_failure(evidence, {}, grade_evidence([], evidence)) == "extraction"
    assert classify_failure(None, stored, None) is None


def test_failure_cause_accepts_evidence_in_any_same_title_document_and_normalizes_whitespace():
    from scripts.eval_search import classify_failure, grade_evidence

    evidence = [{"source": "운영", "text": "서울 74091"}]
    stored = {"운영": ["다른 판", "서울\t74091\t38612"]}
    assert classify_failure(evidence, stored, grade_evidence([], evidence)) == "candidates"


def test_find_leaks_reports_forbidden_text_anywhere_in_results_including_graph_hits():
    """과거 버전 값·남의 비공개 문서 내용은 어떤 결과 자리에도 나오면 안 된다."""
    from scripts.eval_search import find_leaks

    from openarchive.services.search import SearchPassage, SearchVia

    absent = ["2026.9.1\t1.21392", "국내총책 A씨"]
    assert find_leaks([_hit("운영", content="2026.10.1 1.21169")], absent) == []
    assert find_leaks([_hit("운영", preview="2026.9.1  1.21392")], absent) == ["2026.9.1\t1.21392"]
    passage = _hit("운영", passages=(SearchPassage(3, "국내총책 A씨 구속", 1, .5),))
    assert find_leaks([passage], absent) == ["국내총책 A씨"]
    graph = _hit("운영", content="국내총책 A씨", via=SearchVia(D2, "revision", 1))
    assert find_leaks([graph], absent) == ["국내총책 A씨"]
    assert find_leaks([], None) == []


def test_summary_keeps_rank_metrics_to_answerable_queries_and_counts_causes_and_leaks():
    """근거 없음·권한·과거 버전 질문은 정답 문서가 없다 — Recall 0으로 섞으면 순위 지표가 왜곡된다."""
    results = [
        QueryResult("answerable", [D1], {D1}, set(), failure_cause="selection"),
        QueryResult("extraction", [D2], {D1}, set(), failure_cause="extraction"),
        QueryResult("none", [D3], set(), set(), top_score=0.41),
        QueryResult("leak", [D3], set(), set(), leaks=["국내총책 A씨"]),
    ]
    summary = summarize(results, ks=(1,))
    assert summary["queries"] == 4
    assert summary["answerable_queries"] == 2
    assert summary["recall@1"] == pytest.approx(0.5)
    assert summary["mrr"] == pytest.approx(0.5)
    assert summary["misses"] == 1
    assert summary["no_answer_queries"] == 2
    assert summary["leaks"] == 1
    assert summary["cause_selection"] == 1
    assert summary["cause_extraction"] == 1
    legacy = summarize([QueryResult("q", [D1], {D1}, set())], ks=(1,))
    assert "no_answer_queries" not in legacy and "leaks" not in legacy


async def test_evaluator_classifies_from_stored_text_and_records_no_answer_score(migrated_db, monkeypatch):
    import psycopg
    from conftest import insert_test_document, process_all_embedding_jobs
    from scripts import eval_search

    from openarchive.embeddings import FakeProvider

    provider = FakeProvider()
    monkeypatch.setattr(eval_search, "get_provider", lambda: provider)
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await insert_test_document(conn, title="스캔", content="세관장 확인 대상 5.851개 품목",
                                   owner_id="alice", visibility="private", tags=["evaluation"])
        await insert_test_document(conn, title="비밀", content="국내총책 A씨 구속",
                                   owner_id="bob", visibility="private", tags=["evaluation"])
        await process_all_embedding_jobs(conn, provider)
    evalset = {"queries": [
        {"query": "세관장 확인 대상 품목", "relevant": ["스캔"],
         "evidence": [{"source": "스캔", "text": "5,851개 품목"}]},
        {"query": "국내총책 A씨", "relevant": [], "absent": ["국내총책 A씨"]},
    ]}
    results = await eval_search.evaluate(evalset, migrated_db, user_id="alice", tags=["evaluation"])
    assert results[0].failure_cause == "extraction"
    assert results[1].relevant == set()
    assert results[1].leaks == []
    assert results[1].top_score is not None


def test_current_only_leaks_ignore_labeled_revisions_and_report_where_the_past_is():
    """과거 판본 값은 「이전 버전」(via=revision) 자리에 표시돼 나오는 것이 설계다(ARCHITECTURE ⑤).
    현재 판 결과에 섞이면 누출이고, revision 자리에서 찾히는지는 따로 기록한다."""
    from scripts.eval_search import find_in_revisions, find_leaks

    from openarchive.services.search import SearchVia

    past = ["2026.9.1\t1.21392"]
    revision = _hit("계수", content="2026.9.1 1.21392", via=SearchVia(D1, "revision", 1))
    current = _hit("계수", content="2026.10.1 1.21169")
    assert find_leaks([current, revision], past, current_only=True) == []
    assert find_leaks([current, revision], past) == past
    assert find_leaks([_hit("계수", content="2026.9.1 1.21392")], past, current_only=True) == past
    assert find_in_revisions([current, revision], past) == past
    assert find_in_revisions([current], past) == []
    assert find_in_revisions([current], None) == []


def test_leaks_and_past_locations_are_reported_for_answerable_queries_too():
    """누출은 정답 유무와 무관하다 — 정답 있는 질문에 `absent`를 달아도 집계·출력에서 빠지면 안 된다."""
    from scripts.eval_search import render

    results = [
        QueryResult("answerable leak", [D1], {D1}, set(), leaks=["국내총책 A씨"]),
        QueryResult("past", [D1], {D1}, set(), past_in_revisions=["2026.9.1\t1.21392"]),
    ]
    summary = summarize(results, ks=(1,))
    assert summary["leaks"] == 1
    assert summary["past_in_revisions"] == 1
    assert "누출=['국내총책 A씨']" in render(results, summary)
    assert "과거 값은 이전 버전 자리에 있음" in render(results, summary)


def test_summary_without_answerable_queries_skips_rank_metrics():
    summary = summarize([QueryResult("none", [D1], set(), set(), top_score=0.4)], ks=(1,))
    assert summary["queries"] == 1
    assert summary["answerable_queries"] == 0
    assert "recall@1" not in summary and "mrr" not in summary
