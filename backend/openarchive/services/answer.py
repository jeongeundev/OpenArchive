"""ADR-043의 고정 검색 → 근거 → 프롬프트 → 생성 → 응답 파이프라인.

DB 단계와 생성 단계를 나눠 생성 동안 커넥션을 점유하지 않는다.
답변은 DB에 저장하지 않는다(결정 4).
"""

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

import psycopg

from openarchive.answers import AnswerProvider, AnswerUnavailable
from openarchive.embeddings.base import EmbeddingProvider
from openarchive.services.chunking import chunk_text
from openarchive.services.search import SearchHit, SearchPassage, search_documents
from openarchive.services.visibility import VISIBLE_TO_USER

logger = logging.getLogger(__name__)

ASK_K = 5


@dataclass(frozen=True)
class AnswerSource:
    label: int
    document_id: UUID
    title: str
    chunk_index: int
    based_on_version: int
    current_version: int
    revised: bool
    content: str
    cited: bool = False


@dataclass(frozen=True)
class Evidence:
    hits: list[SearchHit]
    sources: list[AnswerSource]
    system: str
    prompt: str


AnswerStatus = Literal["answered", "no_evidence", "disabled", "failed"]


@dataclass(frozen=True)
class AnswerResult:
    status: AnswerStatus
    answer: str | None
    sources: list[AnswerSource]
    hits: list[SearchHit]
    detail: str | None


def _matching_passage(hit: SearchHit, current: str) -> SearchPassage:
    """직전 판 전문을 워커와 같은 청킹으로 나눠 현재 판 청크와 어절이 가장 많이 겹치는 청크를 고른다."""
    chunks = chunk_text(hit.content)
    words = set(current.split())

    def overlap(index):
        other = set(chunks[index].split())
        return len(words & other) / (len(words | other) or 1)

    index = max(range(len(chunks)), key=overlap)
    return SearchPassage(index, chunks[index], hit.based_on_version, hit.score)


async def gather_evidence(
    conn: psycopg.AsyncConnection,
    embedding_provider: EmbeddingProvider,
    *,
    query: str,
    user_id: str | None,
    tags: list[str] | None = None,
    content_type: str | None = None,
    folder_id: UUID | None = None,
    k: int = ASK_K,
    context_chars: int,
) -> Evidence:
    hits = await search_documents(
        conn, embedding_provider, query=query, user_id=user_id,
        tags=tags, content_type=content_type, k=k, folder_id=folder_id,
    )
    versions, current_chunks = {}, {}
    if hits:
        revisions = [hit for hit in hits if hit.via and hit.via.kind == "revision"]
        async with conn.transaction():
            cursor = await conn.execute(
                f"SELECT d.id, d.version FROM documents d "
                f"WHERE d.id = ANY(%(ids)s) AND {VISIBLE_TO_USER}",
                {"ids": list({hit.document_id for hit in hits}), "user": user_id},
            )
            versions = dict(await cursor.fetchall())
            if revisions:
                # revision 히트의 본문은 직전 판 전문이다. 검색이 맞춘 현재 판 청크를 가져와
                # 직전 판에서 같은 자리를 찾는다(검색 SQL은 고치지 않는다 — 코어 diff 0줄).
                cursor = await conn.execute(
                    "SELECT c.document_id, c.chunk_index, c.content FROM document_chunks c "
                    "JOIN unnest(%(ids)s::uuid[], %(versions)s::int[], %(indexes)s::int[]) "
                    "AS r(document_id, version, chunk_index) USING (document_id, version, chunk_index)",
                    {
                        "ids": [hit.document_id for hit in revisions],
                        "versions": [hit.based_on_version + 1 for hit in revisions],
                        "indexes": [hit.chunk_index for hit in revisions],
                    },
                )
                current_chunks = {(row[0], row[1]): row[2] for row in await cursor.fetchall()}
    selected = []
    seen = set()
    remaining = context_chars
    for hit in hits:
        if hit.via and hit.via.kind == "revision":
            current = current_chunks.get((hit.document_id, hit.chunk_index), "")
            passages = (_matching_passage(hit, current),)
        else:
            passages = hit.passages or (hit,)
        for passage in passages:
            content = passage.content.strip()
            if not content or content in seen:
                continue
            if len(content) > remaining:
                if selected:
                    continue
                content = content[:remaining]
            selected.append((hit, passage, content))
            seen.add(passage.content.strip())
            remaining -= len(content)
    sources = []
    for hit, passage, content in selected:
        if hit.document_id not in versions:
            continue
        current = versions[hit.document_id]
        sources.append(AnswerSource(
            label=len(sources) + 1, document_id=hit.document_id, title=hit.title,
            chunk_index=passage.chunk_index, based_on_version=passage.based_on_version,
            current_version=current, revised=passage.based_on_version < current,
            content=content,
        ))
    system = (
        '주어진 근거만 사용해 한국어로 답하세요. 근거에 없으면 '
        '"근거 문서에서 찾을 수 없습니다"라고 답하세요. '
        '주장마다 [번호]로 인용하세요. 이전 버전 근거는 현재 문서와 다를 수 있음을 밝히세요.'
    )
    blocks = []
    for source in sources:
        heading = f"[{source.label}] {source.title} · v{source.based_on_version} 기준"
        if source.revised:
            heading += f" · 현재 v{source.current_version}"
        blocks.append(f"{heading}\n{source.content}")
    prompt = "\n\n".join(blocks) + f"\n\n질문: {query}"
    return Evidence(hits, sources, system, prompt)


async def generate_answer(
    evidence: Evidence, answer_provider: AnswerProvider | None,
) -> AnswerResult:
    if answer_provider is None:
        return AnswerResult("disabled", None, evidence.sources, evidence.hits, None)
    if not evidence.sources:
        return AnswerResult("no_evidence", None, evidence.sources, evidence.hits, None)
    try:
        response = await asyncio.to_thread(
            answer_provider.generate, evidence.system, evidence.prompt,
        )
    except AnswerUnavailable as exc:
        logger.warning("답변 생성 실패 — 검색 결과만 돌려준다: %s", exc)
        return AnswerResult("failed", None, evidence.sources, evidence.hits, "답변 생성에 실패했습니다.")
    labels = {int(label) for label in re.findall(r"\[(\d+)\]", response)}
    sources = [replace(source, cited=source.label in labels) for source in evidence.sources]
    return AnswerResult("answered", response, sources, evidence.hits, None)
