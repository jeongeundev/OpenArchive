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

from openarchive.answers import AnswerProvider, AnswerUnavailable
from openarchive.services.search import SearchHit, search_documents
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


async def gather_evidence(
    conn, embedding_provider, *, query, user_id, tags=None, content_type=None,
    k=ASK_K, context_chars,
) -> Evidence:
    hits = await search_documents(
        conn, embedding_provider, query=query, user_id=user_id,
        tags=tags, content_type=content_type, k=k,
    )
    selected = []
    seen = set()
    remaining = context_chars
    for hit in hits:
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
    versions = {}
    if selected:
        async with conn.transaction():
            cursor = await conn.execute(
                f"SELECT d.id, d.version FROM documents d "
                f"WHERE d.id = ANY(%(ids)s) AND {VISIBLE_TO_USER}",
                {"ids": list({hit.document_id for hit, _, _ in selected}), "user": user_id},
            )
            versions = dict(await cursor.fetchall())
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
        return AnswerResult("failed", None, evidence.sources, evidence.hits, str(exc) or "답변 생성에 실패했습니다.")
    labels = {int(label) for label in re.findall(r"\[(\d+)\]", response)}
    sources = [replace(source, cited=source.label in labels) for source in evidence.sources]
    return AnswerResult("answered", response, sources, evidence.hits, None)
