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


def test_numeric_key_does_not_match_inside_a_longer_number():
    """`8.6%`가 `18.6%` 안에서, `43개`가 `143개` 안에서 맞으면 틀린 값을 맞음으로 센다."""
    for key, wrong, right in [
        ("8.6%", "졸업생 18.6% 증가 [1]", "졸업생 8.6% 증가 [1]"),
        ("43개", "143개 법령 [1]", "43개 법령 [1]"),
        ("1.21392", "계수는 1.213925입니다 [1]", "계수는 1.21392입니다 [1]"),
        ("37,505", "137,505명 [1]", "서울 37505명 [1]"),
    ]:
        item = {"answer_keys": [key]}
        assert grade_answer(item, "answered", wrong, [_source(1)], {D1})["key_hit"] is False, key
        assert grade_answer(item, "answered", right, [_source(1)], {D1})["key_hit"] is True, key
    sources = [_source(1, content="전체 18.6% 증가", cited=False)]
    assert grade_answer({"answer_keys": ["8.6%"]}, "answered", "답 [1]", sources, {D1})["keys_in_sources"] is False


def test_whitespace_between_table_cells_still_separates_numbers():
    """표 셀은 탭·공백으로만 나뉜다. 공백을 지운 뒤 경계를 보면 이웃 셀과 붙어 맞는 값을 놓친다."""
    item = {"answer_keys": ["37,505", "1.21392"]}
    sources = [_source(1, content="서울\t37505\t21152\n9월\t1.21392\t1.21169", cited=False)]

    graded = grade_answer(item, "answered", "37,505 명, 1.21392 [1]", sources, {D1})

    assert graded["keys_in_sources"] is True
    assert graded["key_hit"] is True


def test_key_hit_is_none_without_keys_or_answer():
    assert grade_answer({}, "answered", "답 [1]", [_source(1)], {D1})["key_hit"] is None
    assert grade_answer({"answer_keys": ["1"]}, "failed", None, [_source(1)], {D1})["key_hit"] is False


def test_keys_in_sources_separates_missing_evidence_from_model_misses():
    """답이 근거에 없었으면(추출·검색·예산) 모델 탓이 아니다. 근거에 있는데 못 맞히면 모델의 실패다."""
    item = {"answer_keys": ["30.82", ["4천만 달러", "40 million"]]}
    given = [_source(1, content="제주(30.82㎡) … over USD 40 million", cited=False)]
    cut = [_source(1, content="제주(30.", cited=False)]

    missed = grade_answer(item, "answered", f"{REFUSAL}.", given, {D1})
    absent = grade_answer(item, "answered", f"{REFUSAL}.", cut, {D1})

    assert missed["keys_in_sources"] is True and missed["key_hit"] is False
    assert absent["keys_in_sources"] is False
    assert grade_answer({}, "answered", "답 [1]", given, {D1})["keys_in_sources"] is None


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
        {"status": "answered", "answerable": True, "key_hit": True, "keys_in_sources": True, "given_relevant": True,
         "cited_relevant": True, "refused": False, "leaks": [], "revision_cited": None,
         "latencies": [2.0, 4.0], "answers_differ": False},
        {"status": "answered", "answerable": True, "key_hit": False, "keys_in_sources": True, "given_relevant": True,
         "cited_relevant": False, "refused": True, "leaks": [], "revision_cited": True,
         "latencies": [6.0, 8.0], "answers_differ": True},
        {"status": "no_evidence", "answerable": False, "key_hit": None, "keys_in_sources": None,
         "given_relevant": None, "cited_relevant": None, "refused": True, "leaks": [], "revision_cited": None,
         "latencies": [], "answers_differ": False},
        {"status": "answered", "answerable": False, "key_hit": None, "keys_in_sources": None, "given_relevant": None,
         "cited_relevant": None, "refused": False, "leaks": ["비밀"], "revision_cited": None,
         "latencies": [10.0], "answers_differ": False},
        {"status": "failed", "answerable": True, "key_hit": False, "keys_in_sources": False, "given_relevant": True,
         "cited_relevant": False, "refused": False, "leaks": [], "revision_cited": None,
         "latencies": [120.0], "answers_differ": False},
        # 핵심값을 답하고 덧붙인 부분에서만 거절 문구를 쓴 답 — 맞은 답이지 거절 실패가 아니다
        {"status": "answered", "answerable": True, "key_hit": True, "keys_in_sources": True, "given_relevant": True,
         "cited_relevant": True, "refused": True, "leaks": [], "revision_cited": None,
         "latencies": [], "answers_differ": False},
    ]

    summary = summarize(graded)

    assert summary["queries"] == 6
    assert summary["status_answered"] == 4
    assert summary["status_failed"] == 1
    assert summary["status_no_evidence"] == 1
    assert summary["answerable"] == 4
    assert summary["key_hits"] == 2
    assert summary["key_graded"] == 4
    assert summary["keys_in_sources"] == 3
    assert summary["model_misses"] == 1  # 근거에 있었는데 못 맞힘. 실패(타임아웃)는 근거에 없어 빠진다
    assert summary["given_relevant"] == 4
    assert summary["cited_relevant"] == 2
    assert summary["false_refusals"] == 1
    assert summary["no_answer"] == 2
    assert summary["correct_refusals"] == 1
    assert summary["leaks"] == 1
    assert summary["revision_cited"] == 1
    assert summary["revision_graded"] == 1
    assert summary["answers_differ"] == 1
    assert summary["generation_calls"] == 6
    assert summary["latency_p50_s"] == 6.0
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


async def test_evaluator_runs_the_api_pipeline_and_resets_the_model_cache_before_each_generation(migrated_db):
    """Ollama는 같은 프롬프트 앞부분의 KV 캐시를 재사용해 반복 생성이 10배 빨라진다(실측 16초 → 1.5초).
    같은 문서를 근거로 받는 바로 앞 질문도 캐시를 남긴다. 그래서 측정마다 초기화하고 그 뒤 생성만 잰다.
    """
    import psycopg
    from conftest import insert_test_document, process_all_embedding_jobs
    from scripts.eval_ask import evaluate

    from openarchive.answers import FakeAnswerProvider
    from openarchive.embeddings import FakeProvider

    events = []

    class RecordingProvider(FakeAnswerProvider):
        def generate(self, system, prompt):
            events.append("generate")
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
        evalset, migrated_db, embedding_provider=provider, answer_provider=RecordingProvider(),
        reset_cache=lambda: events.append("reset"),
        user_id="alice", tags=["evaluation"], context_chars=6000, repeats=3,
    )

    ok, secret = results
    assert ok["status"] == "answered"
    assert ok["cited_relevant"] is True and ok["key_hit"] is True
    assert [source["document_id"] for source in ok["sources"]] == [str(allowed)]
    assert len(ok["latencies"]) == 3 and ok["answers_differ"] is False
    assert ok["manual"] == {"verdict": None, "note": None}
    assert secret["leaks"] == []
    assert ok["answerable"] is True and secret["answerable"] is False
    # 생성마다 바로 앞에 초기화가 한 번. 근거 0건이면 모델을 부르지 않는다(서비스 계약).
    generations = len(ok["latencies"]) + len(secret["latencies"])
    assert events == ["reset", "generate"] * generations


async def test_evaluator_grades_the_first_generation_and_flags_differing_repeats(migrated_db):
    """반복의 답이 갈리면 어느 답을 채점했는지 정해져 있어야 한다 — 캐시 없이 처음 받은 답(첫 생성)이다."""
    import psycopg
    from conftest import insert_test_document, process_all_embedding_jobs
    from scripts.eval_ask import evaluate

    from openarchive.embeddings import FakeProvider

    replies = iter(["13,784명입니다 [1]", f"{REFUSAL}.", f"{REFUSAL}."])

    class DriftingProvider:
        name = "drifting"

        def generate(self, system, prompt):
            return next(replies)

    provider = FakeProvider()
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await insert_test_document(conn, title="접수", content="졸업생 13,784명이 늘었다.",
                                   owner_id="alice", visibility="private", tags=["evaluation"])
        await process_all_embedding_jobs(conn, provider)
    evalset = {"queries": [{"id": "n", "query": "졸업생 13,784명이 늘었다", "relevant": ["접수"],
                            "answer_keys": ["13,784명"]}]}

    [graded] = await evaluate(
        evalset, migrated_db, embedding_provider=provider, answer_provider=DriftingProvider(),
        reset_cache=lambda: None, user_id="alice", tags=["evaluation"], context_chars=6000, repeats=3,
    )

    assert graded["answer"] == "13,784명입니다 [1]"
    assert graded["key_hit"] is True and graded["refused"] is False
    assert graded["answers_differ"] is True
    assert graded["answerable"] is True
