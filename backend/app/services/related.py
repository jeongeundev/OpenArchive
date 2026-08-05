"""문서의 관련 문서와 동일 텍스트 문서를 조회하는 서비스."""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.services.search import EF_SEARCH, MAX_K

RELATED_SQL = """
WITH me AS (
  SELECT avg(embedding) AS v FROM document_chunks WHERE document_id = %(id)s
),
cand AS (
  SELECT c.document_id, c.embedding <=> (SELECT v FROM me) AS dist
  FROM document_chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.document_id <> %(id)s
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY c.embedding <=> (SELECT v FROM me)
  LIMIT %(k)s * 10
),
best AS (
  SELECT DISTINCT ON (document_id) document_id, dist
  FROM cand
  ORDER BY document_id, dist
)
SELECT d.id, d.title, d.tags, 1 - b.dist AS score
FROM best b JOIN documents d ON d.id = b.document_id
ORDER BY b.dist LIMIT %(k)s
"""

IDENTICAL_SQL = """
SELECT o.id, o.title
FROM documents me
JOIN documents o
  ON o.content_hash = me.content_hash AND o.id <> me.id
WHERE me.id = %(id)s
  AND (o.visibility = 'public' OR o.owner_id = %(user)s)
ORDER BY o.created_at, o.id
"""

TAG_SUGGESTION_SQL = """
WITH me AS (
  SELECT avg(embedding) AS v FROM document_chunks WHERE document_id = %(id)s
),
cand AS (
  SELECT c.document_id, c.embedding <=> (SELECT v FROM me) AS dist
  FROM document_chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.document_id <> %(id)s
    AND (d.visibility = 'public' OR d.owner_id = %(user)s)
  ORDER BY c.embedding <=> (SELECT v FROM me)
  LIMIT 100
),
best AS (
  SELECT DISTINCT ON (document_id) document_id, dist
  FROM cand
  ORDER BY document_id, dist
),
neighbors AS (
  SELECT document_id FROM best ORDER BY dist LIMIT 10
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
    """대상 청크 평균에 가까운 문서와 동일 텍스트 문서를 반환한다."""
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k는 1 이상 {MAX_K} 이하여야 한다: {k}")

    params = {"id": document_id, "user": user_id, "k": k}
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

        await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        await conn.execute("SET LOCAL random_page_cost = 1.1")
        related_cur = await conn.execute(RELATED_SQL, params)
        rows = await related_cur.fetchall()

    return RelatedResult(
        items=[
            RelatedDocument(
                document_id=row[0],
                title=row[1],
                tags=row[2],
                score=float(row[3]),
            )
            for row in rows
        ],
        identical=identical,
        based_on_version=based_on_version,
        reason=None,
    )


async def suggest_tags(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
    limit: int = 5,
) -> TagSuggestionResult:
    """유사 문서에 달린 태그를 빈도순으로 추천한다."""
    params = {"id": document_id, "user": user_id, "limit": limit}
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

        await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        await conn.execute("SET LOCAL random_page_cost = 1.1")
        suggestion_cur = await conn.execute(TAG_SUGGESTION_SQL, params)
        rows = await suggestion_cur.fetchall()

    return TagSuggestionResult(
        items=[TagSuggestion(tag=row[0], freq=row[1]) for row in rows],
        based_on_version=based_on_version,
        reason=None,
    )
