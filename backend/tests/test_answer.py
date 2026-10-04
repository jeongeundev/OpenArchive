import inspect

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.answers import AnswerUnavailable, FakeAnswerProvider
from openarchive.embeddings import FakeProvider
from openarchive.services import answer
from openarchive.services.search import search_documents


class RecordingProvider:
    name = "recording"

    def generate(self, system, prompt):
        self.system, self.prompt = system, prompt
        return "인용 [2] [999]"


class NeverProvider:
    name = "never"

    def generate(self, system, prompt):
        pytest.fail("모델을 호출하면 안 됩니다")


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as connection:
        yield connection


async def seed(conn, **kwargs):
    document_id = await insert_test_document(conn, **kwargs)
    await process_all_embedding_jobs(conn, FakeProvider())
    return document_id


async def gather(conn, **kwargs):
    return await answer.gather_evidence(
        conn, FakeProvider(), query="정합성 근거", user_id="alice", context_chars=6000, **kwargs
    )


async def test_source_versions_and_generation_after_connection_closed(conn):
    document_id = await seed(conn, title="근거", content="정합성 근거")
    evidence = await gather(conn)
    assert all(not source.cited for source in evidence.sources)
    await conn.close()
    assert "conn" not in inspect.signature(answer.generate_answer).parameters
    result = await answer.generate_answer(evidence, FakeAnswerProvider())
    assert result.status == "answered" and "[1]" in result.answer
    source = result.sources[0]
    assert (source.document_id, source.title, source.chunk_index) == (document_id, "근거", 0)
    assert source.based_on_version == source.current_version == 1
    assert source.revised is False and source.cited is True
    assert source.content in evidence.prompt


async def test_previous_version_is_marked(conn):
    document_id = await seed(conn, title="개정", content="개정 전 정합성 근거")
    await conn.execute(
        "UPDATE documents SET version=2, content=%s, content_hash=%s WHERE id=%s",
        ("개정 후 정합성 근거", "version-two", document_id),
    )
    await process_all_embedding_jobs(conn, FakeProvider())
    evidence = await gather(conn)
    old = next(source for source in evidence.sources if source.based_on_version == 1)
    assert old.current_version == 2 and old.revised is True
    assert f"[{old.label}] 개정 · v1 기준 · 현재 v2" in evidence.prompt


async def test_empty_evidence_and_disabled_precedence(conn):
    evidence = await gather(conn, tags=["없는 태그"])
    result = await answer.generate_answer(evidence, NeverProvider())
    assert result.status == "no_evidence" and result.sources == [] and result.hits == []
    assert (await answer.generate_answer(evidence, None)).status == "disabled"


@pytest.mark.parametrize("user", ["alice", None])
async def test_private_evidence_is_absent(conn, user):
    hidden = await seed(conn, title="비밀", content="정합성 근거 비밀고유문자열", owner_id="bob", visibility="private")
    await seed(conn, title="공개", content="공개 설명")
    evidence = await answer.gather_evidence(conn, FakeProvider(), query="정합성 근거", user_id=user, context_chars=6000)
    provider = RecordingProvider()
    result = await answer.generate_answer(evidence, provider)
    assert hidden not in [hit.document_id for hit in result.hits]
    assert hidden not in [source.document_id for source in result.sources]
    assert "비밀고유문자열" not in provider.prompt


async def test_revoked_between_search_and_version_query(conn, monkeypatch):
    document_id = await seed(conn, title="공개", content="정합성 근거", owner_id="bob")
    async def revoke(*args, **kwargs):
        hits = await search_documents(*args, **kwargs)
        await conn.execute("UPDATE documents SET visibility='private' WHERE id=%s", (document_id,))
        return hits
    monkeypatch.setattr(answer, "search_documents", revoke)
    evidence = await gather(conn)
    assert evidence.hits and evidence.sources == []
    assert "정합성 근거" not in evidence.prompt.split("질문:")[0]


@pytest.mark.parametrize("message", ["연결 실패", ""])
async def test_disabled_and_failures_keep_search(conn, message):
    await seed(conn, title="근거", content="정합성 근거")
    evidence = await gather(conn)
    expected = await search_documents(conn, FakeProvider(), query="정합성 근거", user_id="alice", k=5)
    disabled = await answer.generate_answer(evidence, None)
    assert disabled.status == "disabled" and disabled.answer is None
    assert disabled.hits == expected
    class FailingProvider:
        name = "failure"
        def generate(self, system, prompt):
            raise AnswerUnavailable(message)
    failed = await answer.generate_answer(evidence, FailingProvider())
    assert failed.status == "failed" and failed.answer is None and failed.detail
    assert failed.hits == evidence.hits and failed.sources == evidence.sources
    class BrokenProvider:
        name = "broken"
        def generate(self, system, prompt):
            raise RuntimeError("결함")
    with pytest.raises(RuntimeError, match="결함"):
        await answer.generate_answer(evidence, BrokenProvider())


async def test_budget_duplicates_labels_and_cited(conn):
    for title, content in [("A", "정합성 근거"), ("B", "정합성 근거"), ("C", "별도 대목"), ("D", "세 번째 대목")]:
        await seed(conn, title=title, content=content)
    evidence = await gather(conn)
    assert len(evidence.sources) == 3
    assert len({source.content for source in evidence.sources}) == 3
    assert [source.label for source in evidence.sources] == [1, 2, 3]
    result = await answer.generate_answer(evidence, RecordingProvider())
    assert [source.cited for source in result.sources] == [False, True, False]
    small = await answer.gather_evidence(conn, FakeProvider(), query="정합성 근거", user_id="alice", context_chars=3)
    assert len(small.sources) == 1 and len(small.sources[0].content) == 3
    assert sum(len(source.content) for source in small.sources) <= 3


@pytest.mark.parametrize("k", [0, 21])
async def test_invalid_k(conn, k):
    with pytest.raises(ValueError):
        await gather(conn, k=k)


async def test_passage_order_and_skipping_oversized_following_passage(conn, monkeypatch):
    from dataclasses import replace

    from openarchive.services.search import SearchPassage

    await seed(conn, title="대목", content="정합성 근거")

    async def with_passages(*args, **kwargs):
        hits = await search_documents(*args, **kwargs)
        return [replace(hits[0], passages=(
            SearchPassage(2, " 첫 대목 ", 1, 0.9),
            SearchPassage(3, "예산보다 긴 대목" * 20, 1, 0.8),
            SearchPassage(4, "끝", 1, 0.7),
        ))]

    monkeypatch.setattr(answer, "search_documents", with_passages)
    evidence = await answer.gather_evidence(
        conn, FakeProvider(), query="정합성 근거", user_id="alice", context_chars=6,
    )
    assert [(source.label, source.chunk_index, source.content) for source in evidence.sources] == [
        (1, 2, "첫 대목"), (2, 4, "끝"),
    ]
    assert sum(len(source.content) for source in evidence.sources) <= 6
