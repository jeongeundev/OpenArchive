"""근거 기반 답변 평가(#96 c)의 채점 규칙을 고정한다.

모델 확정과 #168 전후 비교를 이 수치로 말하므로, 무엇을 맞음으로 세는지(정답 문서 인용·핵심값 포함·
거절·누출·과거 판본 인용)가 흔들리면 비교가 무의미해진다. 자동 채점은 문자열 포함이며 주장과 인용의
의미 일치는 사람이 `manual` 칸에 판정한다.
"""

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.eval_ask import REFUSAL, check_providers, grade_answer, percentile, summarize

from openarchive.services.answer import AnswerSource

D1, D2 = UUID(int=1), UUID(int=2)


def _source(label, document_id=D1, *, title="문서", cited=True, revised=False, content="본문"):
    return AnswerSource(
        label=label, document_id=document_id, title=title, chunk_index=0,
        based_on_version=1, current_version=2 if revised else 1, revised=revised,
        content=content, cited=cited,
    )


def test_key_hit_requires_every_key_after_whitespace_normalization():
    item = {"answer_keys": ["13,784명", "8.6%"]}

    hit = grade_answer(item, "answered", "졸업생이  13,784명\n(8.6%) 늘었습니다 [1]", [_source(1)], {D1})
    miss = grade_answer(item, "answered", "13,784명 늘었습니다 [1]", [_source(1)], {D1})

    assert hit["key_hit"] is True
    assert miss["key_hit"] is False
    assert miss["missing_keys"] == ["8.6%"]


def test_key_hit_ignores_thousands_separators_and_accepts_alternative_spellings():
    """표의 셀 값은 쉼표 없이(`74091`) 저장되고, 모델은 같은 값을 다른 단위로 쓸 수 있다."""
    item = {"answer_keys": ["74,091", ["4천만 달러", "4,000만 달러", "40 million"]]}

    assert grade_answer(item, "answered", "서울 74091명, 4,000만 달러 [1]", [_source(1)], {D1})["key_hit"] is True
    missing = grade_answer(item, "answered", "서울 74,091명 [1]", [_source(1)], {D1})
    assert missing["missing_keys"] == [["4천만 달러", "4,000만 달러", "40 million"]]


def test_key_hit_is_none_without_keys_or_answer():
    assert grade_answer({}, "answered", "답 [1]", [_source(1)], {D1})["key_hit"] is None
    assert grade_answer({"answer_keys": ["1"]}, "failed", None, [_source(1)], {D1})["key_hit"] is False


def test_citation_separates_given_from_cited_and_reports_wrong_citations():
    """정답 문서를 근거로 받았는데 다른 문서만 인용하면 검색이 아니라 모델의 실패다."""
    sources = [_source(1, D1, title="정답", cited=False), _source(2, D2, title="엉뚱", cited=True)]

    graded = grade_answer({}, "answered", "답 [2]", sources, {D1})

    assert graded["given_relevant"] is True
    assert graded["cited_relevant"] is False
    assert graded["cited_outside"] == ["엉뚱"]


def test_citation_fields_are_none_for_questions_without_relevant_documents():
    graded = grade_answer({}, "answered", "답 [1]", [_source(1)], set())

    assert graded["given_relevant"] is None
    assert graded["cited_relevant"] is None


def test_refusal_counts_no_evidence_status_and_refusal_phrase_but_not_an_invented_answer():
    assert grade_answer({}, "no_evidence", None, [], set())["refused"] is True
    assert grade_answer({}, "answered", f"{REFUSAL}.", [_source(1, cited=False)], set())["refused"] is True
    assert grade_answer({}, "answered", "300명입니다 [1]", [_source(1)], set())["refused"] is False


def test_refusal_on_answerable_question_is_recorded_too():
    """정답 근거가 있는데 거절하면 거절 성공이 아니라 실패다 — 요약이 따로 센다."""
    graded = grade_answer({"answer_keys": ["37,505"]}, "answered", f"{REFUSAL}.", [_source(1, cited=False)], {D1})

    assert graded["refused"] is True
    assert graded["key_hit"] is False


def test_leaks_find_forbidden_text_in_the_answer_only():
    item = {"answer_absent": ["국내총책 A씨"]}

    leaked = grade_answer(item, "answered", "국내총책  A씨가 구속됐습니다 [1]", [_source(1)], set())
    clean = grade_answer(item, "answered", f"{REFUSAL}.", [_source(1, content="국내총책 A씨", cited=False)], set())

    assert leaked["leaks"] == ["국내총책 A씨"]
    assert clean["leaks"] == []


def test_answer_from_revision_requires_the_value_to_be_cited_from_a_revised_source():
    """과거 값을 현재 판 근거로 인용하면(또는 아예 인용하지 않으면) 과거를 현재로 말한 것이다."""
    item = {"answer_keys": ["1.21392"], "answer_from_revision": True}
    past = [_source(1, title="계수", revised=True, content="1.21392"), _source(2, title="계수", cited=False)]
    current = [_source(1, title="계수", revised=True, cited=False), _source(2, title="계수", content="1.21392")]

    assert grade_answer(item, "answered", "1.21392입니다 [1]", past, {D1})["revision_cited"] is True
    assert grade_answer(item, "answered", "1.21392입니다 [2]", current, {D1})["revision_cited"] is False
    assert grade_answer({}, "answered", "답 [1]", past, {D1})["revision_cited"] is None


def test_percentile_uses_nearest_rank():
    values = [5.0, 1.0, 3.0, 2.0, 4.0]

    assert percentile(values, 50) == 3.0
    assert percentile(values, 95) == 5.0
    assert percentile([7.0], 95) == 7.0


def test_summary_keeps_answerable_and_no_answer_counts_apart():
    graded = [
        {"status": "answered", "relevant": True, "key_hit": True, "given_relevant": True,
         "cited_relevant": True, "refused": False, "leaks": [], "revision_cited": None,
         "latencies": [2.0, 4.0], "answers_differ": False},
        {"status": "answered", "relevant": True, "key_hit": False, "given_relevant": True,
         "cited_relevant": False, "refused": True, "leaks": [], "revision_cited": True,
         "latencies": [6.0, 8.0], "answers_differ": True},
        {"status": "no_evidence", "relevant": False, "key_hit": None, "given_relevant": None,
         "cited_relevant": None, "refused": True, "leaks": [], "revision_cited": None,
         "latencies": [], "answers_differ": False},
        {"status": "answered", "relevant": False, "key_hit": None, "given_relevant": None,
         "cited_relevant": None, "refused": False, "leaks": ["비밀"], "revision_cited": None,
         "latencies": [10.0], "answers_differ": False},
        {"status": "failed", "relevant": True, "key_hit": False, "given_relevant": True,
         "cited_relevant": False, "refused": False, "leaks": [], "revision_cited": None,
         "latencies": [120.0], "answers_differ": False},
    ]

    summary = summarize(graded)

    assert summary["queries"] == 5
    assert summary["status_answered"] == 3
    assert summary["status_failed"] == 1
    assert summary["status_no_evidence"] == 1
    assert summary["answerable"] == 3
    assert summary["key_hits"] == 1
    assert summary["key_graded"] == 3
    assert summary["given_relevant"] == 3
    assert summary["cited_relevant"] == 1
    assert summary["false_refusals"] == 1
    assert summary["no_answer"] == 2
    assert summary["correct_refusals"] == 1
    assert summary["leaks"] == 1
    assert summary["revision_cited"] == 1
    assert summary["revision_graded"] == 1
    assert summary["answers_differ"] == 1
    assert summary["generation_calls"] == 6
    assert summary["latency_p50_s"] == 8.0
    assert summary["latency_p95_s"] == 120.0


def test_check_providers_refuses_fake_embeddings_and_answers():
    """fake 벡터나 fake 답변의 수치는 모델 품질이 아니다 — eval_search.py와 같은 원칙."""
    check_providers("local", "ollama")
    for embedding, answer in [("fake", "ollama"), ("local", "fake"), ("local", "off")]:
        with pytest.raises(SystemExit):
            check_providers(embedding, answer)


def test_committed_formats_evalset_keys_are_well_formed():
    evalset = json.loads((ROOT / "scripts" / "eval" / "formats.json").read_text())
    queries = evalset["queries"]

    for item in queries:
        if item["relevant"]:
            assert item.get("answer_keys"), item["id"]
        for key in item.get("answer_keys", []):
            alternatives = key if isinstance(key, list) else [key]
            assert alternatives and all(text.strip() for text in alternatives), item["id"]
    assert any(item.get("answer_from_revision") for item in queries)
    assert all(item.get("answer_absent") for item in queries if "absent" in item)


async def test_evaluator_runs_the_api_pipeline_and_repeats_generation_only(migrated_db):
    import psycopg
    from conftest import insert_test_document, process_all_embedding_jobs
    from scripts.eval_ask import evaluate

    from openarchive.answers import FakeAnswerProvider
    from openarchive.embeddings import FakeProvider

    class CountingProvider(FakeAnswerProvider):
        calls = 0

        def generate(self, system, prompt):
            CountingProvider.calls += 1
            return super().generate(system, prompt)

    provider = FakeProvider()
    text = "알림이 유실돼도 다음 폴링이 처리한다."
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        allowed = await insert_test_document(conn, title="운영", content=text,
                                             owner_id="alice", visibility="private", tags=["evaluation"])
        await insert_test_document(conn, title="비밀", content="국내총책 A씨 구속",
                                   owner_id="bob", visibility="private", tags=["evaluation"])
        await process_all_embedding_jobs(conn, provider)
    evalset = {"queries": [
        {"id": "ok", "category": "조항", "query": "알림이 유실돼도 다음 폴링이 처리한다",
         "relevant": ["운영"], "answer_keys": ["근거 문서"]},
        {"id": "secret", "category": "권한", "query": "국내총책 A씨", "relevant": [],
         "answer_absent": ["국내총책 A씨"]},
    ]}

    results = await evaluate(
        evalset, migrated_db, embedding_provider=provider, answer_provider=CountingProvider(),
        user_id="alice", tags=["evaluation"], context_chars=6000, repeats=3,
    )

    ok, secret = results
    assert ok["status"] == "answered"
    assert ok["cited_relevant"] is True and ok["key_hit"] is True
    assert [source["document_id"] for source in ok["sources"]] == [str(allowed)]
    assert len(ok["latencies"]) == 3 and ok["answers_differ"] is False
    assert ok["manual"] == {"verdict": None, "note": None}
    assert secret["leaks"] == []
    # 예열 1회 + 근거 있는 질문 3회. 근거 0건이면 모델을 부르지 않는다(서비스 계약).
    assert CountingProvider.calls == 1 + len(ok["latencies"]) + len(secret["latencies"])
