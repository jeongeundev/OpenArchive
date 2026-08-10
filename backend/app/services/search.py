"""정형 필터와 벡터 유사도를 한 SQL로 결합하는 검색 서비스."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.embeddings.base import EmbeddingProvider
from app.services.visibility import VISIBLE_TO_USER
from app.vectors import to_pgvector_literal

EF_SEARCH = 200
CANDIDATE_MULTIPLIER = 5
# ADR-011 보강 4: 과다 조회 LIMIT(k * CANDIDATE_MULTIPLIER)이 ef_search에 닿으면
# HNSW가 에러 없이 행을 덜 돌려준다(실측: ef_search=200에서 LIMIT 200 → 193행).
# 등호에서도 모자라므로 상한에서 여유가 남아야 한다 — 여기서는 20 * 5 = 100이다.
# 배수가 다른 호출부(관련 문서·태그 추천)는 자기 배수를 EF_SEARCH에서 역산한다
# (app/services/related.py). 이 불변식은 test_related.py가 지킨다.
MAX_K = 20

SEARCH_SQL = f"""
WITH candidates AS (
    SELECT c.document_id, c.chunk_index, c.content, c.version,
           c.embedding <=> %(qvec)s::vector AS dist
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND {VISIBLE_TO_USER}
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(k)s * {CANDIDATE_MULTIPLIER}
),
best_per_doc AS (
    SELECT DISTINCT ON (document_id) *
    FROM candidates
    ORDER BY document_id, dist
)
SELECT d.id, d.title, d.filename, d.tags, d.content_type,
       b.chunk_index, b.content, 1 - b.dist AS score, b.version
FROM best_per_doc b
JOIN documents d ON d.id = b.document_id
ORDER BY b.dist
LIMIT %(k)s
"""


async def apply_vector_search_settings(conn: psycopg.AsyncConnection) -> None:
    """벡터 정렬 트랜잭션에 필요한 두 설정을 건다 (ADR-011 보강 4·5).

    항상 짝으로 걸어야 한다 — `random_page_cost`만 빠져도 플래너가 HNSW를 아예 고르지
    않는다. 호출부마다 두 줄을 반복하면 한쪽을 빠뜨려도 에러 없이 느려질 뿐이라
    한 곳에 모은다. SET LOCAL은 바인딩할 수 없고, 두 값은 사용자 입력이 아닌 모듈 상수다.
    """
    await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
    await conn.execute("SET LOCAL random_page_cost = 1.1")


@dataclass(frozen=True)
class SearchHit:
    document_id: UUID
    title: str
    filename: str | None
    tags: list[str]
    content_type: str
    chunk_index: int
    content: str
    score: float
    based_on_version: int


async def search_documents(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    *,
    query: str,
    user_id: str | None = None,
    tags: list[str] | None = None,
    content_type: str | None = None,
    k: int = 10,
) -> list[SearchHit]:
    """질의 텍스트를 임베딩해 정형 필터와 함께 단일 SQL로 검색한다."""
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k는 1 이상 {MAX_K} 이하여야 한다: {k}")

    query_vector = (await asyncio.to_thread(provider.embed, [query]))[0]
    params = {
        "qvec": to_pgvector_literal(query_vector),
        # 빈 배열은 "태그를 고르지 않았다"이므로 NULL로 정규화한다. 그대로 넘기면
        # `d.tags && '{}'`가 어느 행에서도 참이 아니라 에러 없이 결과가 0건이 된다.
        # 라우터가 아니라 여기 두는 이유: MCP 서버도 이 함수를 그대로 재사용한다.
        "tags": tags or None,
        "ctype": content_type,
        "user": user_id,
        "k": k,
    }

    async with conn.transaction():
        await apply_vector_search_settings(conn)
        cur = await conn.execute(SEARCH_SQL, params)
        rows = await cur.fetchall()

    return [
        SearchHit(
            document_id=row[0],
            title=row[1],
            filename=row[2],
            tags=row[3],
            content_type=row[4],
            chunk_index=row[5],
            content=row[6],
            score=float(row[7]),
            based_on_version=row[8],
        )
        for row in rows
    ]
