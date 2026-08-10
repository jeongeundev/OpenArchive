"""문서의 관련 문서와 동일 텍스트 문서를 조회하는 서비스."""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.services.search import MAX_K
from app.services.visibility import VISIBLE_TO_USER

# 태그 빈도를 셀 이웃 문서 수. 추천 개수(limit)와 다르다 — 이웃에서 모은 태그를
# 빈도순으로 정렬한 뒤 limit개를 자른다.
NEIGHBOR_LIMIT = 10

RELATED_SQL = f"""
SELECT d.id, d.title, d.tags, e.kind, e.score
FROM document_edges e
JOIN documents d ON d.id = e.dst_document_id
WHERE e.src_document_id = %(id)s
  AND {VISIBLE_TO_USER}
ORDER BY e.kind, e.score DESC, d.id
LIMIT %(k)s
"""

IDENTICAL_SQL = f"""
SELECT d.id, d.title
FROM documents me
JOIN documents d
  ON d.content_hash = me.content_hash AND d.id <> me.id
WHERE me.id = %(id)s
  AND {VISIBLE_TO_USER}
ORDER BY d.created_at, d.id
"""

TAG_SUGGESTION_SQL = f"""
WITH neighbors AS (
  SELECT e.dst_document_id AS document_id
  FROM document_edges e
  JOIN documents d ON d.id = e.dst_document_id
  WHERE e.src_document_id = %(id)s
    AND {VISIBLE_TO_USER}
  ORDER BY e.kind, e.score DESC, d.id
  LIMIT {NEIGHBOR_LIMIT}
)
SELECT t.tag, count(*) AS freq
FROM neighbors n
JOIN documents d ON d.id = n.document_id
CROSS JOIN LATERAL unnest(d.tags) AS t(tag)
WHERE NOT (t.tag = ANY(%(current_tags)s::text[]))
GROUP BY t.tag ORDER BY freq DESC, t.tag LIMIT %(limit)s
"""


@dataclass(frozen=True)
class RelatedDocument:
    document_id: UUID
    title: str
    tags: list[str]
    kind: str
    score: float


@dataclass(frozen=True)
class IdenticalDocument:
    document_id: UUID
    title: str


@dataclass(frozen=True)
class RelatedResult:
    items: list[RelatedDocument]
    identical: list[IdenticalDocument]
    based_on_version: int | None
    reason: str | None


@dataclass(frozen=True)
class TagSuggestion:
    tag: str
    freq: int


@dataclass(frozen=True)
class TagSuggestionResult:
    items: list[TagSuggestion]
    based_on_version: int | None
    reason: str | None


async def _get_chunk_state(
    conn: psycopg.AsyncConnection, params: dict[str, object]
) -> tuple[int, int | None]:
    chunk_cur = await conn.execute(
        "SELECT count(*), min(version) FROM document_chunks WHERE document_id = %(id)s",
        params,
    )
    return await chunk_cur.fetchone()


async def find_related(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
    k: int = 10,
) -> RelatedResult:
    """저장된 관계와 동일 텍스트 문서를 반환한다."""
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k는 1 이상 {MAX_K} 이하여야 한다: {k}")

    params = {
        "id": document_id,
        "user": user_id,
        "k": k,
    }
    async with conn.transaction():
        chunk_count, based_on_version = await _get_chunk_state(conn, params)

        identical_cur = await conn.execute(IDENTICAL_SQL, params)
        identical = [
            IdenticalDocument(document_id=row[0], title=row[1])
            for row in await identical_cur.fetchall()
        ]

        if chunk_count == 0:
            return RelatedResult(
                items=[],
                identical=identical,
                based_on_version=None,
                reason="not_indexed",
            )

        related_cur = await conn.execute(RELATED_SQL, params)
        rows = await related_cur.fetchall()

    reason = None if rows else "no_edges"
    return RelatedResult(
        items=[
            RelatedDocument(
                document_id=row[0],
                title=row[1],
                tags=row[2],
                kind=row[3],
                score=float(row[4]),
            )
            for row in rows
        ],
        identical=identical,
        based_on_version=based_on_version,
        reason=reason,
    )


async def suggest_tags(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
    limit: int = 5,
) -> TagSuggestionResult:
    """유사 문서에 달린 태그를 빈도순으로 추천한다."""
    params = {
        "id": document_id,
        "user": user_id,
        "limit": limit,
    }
    async with conn.transaction():
        chunk_count, based_on_version = await _get_chunk_state(conn, params)
        if chunk_count == 0:
            return TagSuggestionResult(
                items=[], based_on_version=None, reason="not_indexed"
            )

        tags_cur = await conn.execute(
            "SELECT tags FROM documents WHERE id = %(id)s", params
        )
        params["current_tags"] = (await tags_cur.fetchone())[0]

        suggestion_cur = await conn.execute(TAG_SUGGESTION_SQL, params)
        rows = await suggestion_cur.fetchall()

    return TagSuggestionResult(
        items=[TagSuggestion(tag=row[0], freq=row[1]) for row in rows],
        based_on_version=based_on_version,
        reason=None,
    )
