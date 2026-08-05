"""정형 필터와 벡터 유사도를 한 SQL로 결합하는 검색 서비스."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.embeddings.base import EmbeddingProvider
from app.vectors import to_pgvector_literal

EF_SEARCH = 200
CANDIDATE_MULTIPLIER = 5
# ADR-011 보강 4: 과다 조회 LIMIT(k * CANDIDATE_MULTIPLIER)이 ef_search에 닿으면
# HNSW가 에러 없이 행을 덜 돌려준다. EF_SEARCH // CANDIDATE_MULTIPLIER로 두면
# 상한에서 정확히 등호가 되어 여유가 사라지므로, ADR이 안전하다고 못 박은 20을 쓴다
# (관련 문서·태그 추천의 k*10까지 함께 본 값이다).
MAX_K = 20

SEARCH_SQL = f"""
WITH candidates AS (
    SELECT c.document_id, c.chunk_index, c.content,
           c.embedding <=> %(qvec)s::vector AS dist
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND (d.visibility = 'public' OR d.owner_id = %(user)s)
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(k)s * {CANDIDATE_MULTIPLIER}
),
best_per_doc AS (
    SELECT DISTINCT ON (document_id) *
    FROM candidates
    ORDER BY document_id, dist
)
SELECT d.id, d.title, d.tags, d.content_type,
       b.chunk_index, b.content, 1 - b.dist AS score
FROM best_per_doc b
JOIN documents d ON d.id = b.document_id
ORDER BY b.dist
LIMIT %(k)s
"""


@dataclass(frozen=True)
class SearchHit:
    document_id: UUID
    title: str
    tags: list[str]
    content_type: str
    chunk_index: int
    content: str
    score: float


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
        # SET LOCAL은 바인딩할 수 없다. 두 값은 사용자 입력이 아닌 모듈 상수다.
        await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        await conn.execute("SET LOCAL random_page_cost = 1.1")
        cur = await conn.execute(SEARCH_SQL, params)
        rows = await cur.fetchall()

    return [
        SearchHit(
            document_id=row[0],
            title=row[1],
            tags=row[2],
            content_type=row[3],
            chunk_index=row[4],
            content=row[5],
            score=float(row[6]),
        )
        for row in rows
    ]
