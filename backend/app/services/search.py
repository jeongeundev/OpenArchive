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
# 관련 문서·태그 추천은 저장된 edge를 읽으므로 더 이상 벡터 정렬을 하지 않는다
# (ADR-029). 남은 벡터 정렬 호출부는 이 모듈과 008 트리거뿐이며, 트리거는 자기
# 함수 정의의 SET으로 같은 ef_search를 건다. 이 불변식은
# test_search.py·test_related.py가 지킨다.
MAX_K = 20
GRAPH_MAX_DEPTH = 2
# 코사인 거리는 최대 2다. 한 단계마다 그 범위만큼 벌려 직접 결과가 관계 확장보다
# 항상 앞서게 하고, 동시에 기존 `score = 1 - dist` 정렬 의미를 유지한다.
GRAPH_DISTANCE_PENALTY = 2.0

SEARCH_SQL = f"""
WITH RECURSIVE candidates AS (
    SELECT c.document_id, c.chunk_index,
           (
               SELECT string_agg(context.content, E'\n\n' ORDER BY context.chunk_index)
               FROM document_chunks context
               WHERE context.document_id = c.document_id
                 AND context.chunk_index BETWEEN c.chunk_index - 1 AND c.chunk_index + 1
           ) AS content,
           c.version,
           c.embedding <=> %(qvec)s::vector AS dist
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND {VISIBLE_TO_USER}
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(k)s * {CANDIDATE_MULTIPLIER}
),
direct_documents AS (
    SELECT DISTINCT document_id FROM candidates
),
walk AS (
    SELECT c.document_id, c.chunk_index, c.content, c.version, c.dist,
           NULL::uuid AS via_document_id, NULL::text AS via_kind, 0 AS depth,
           ARRAY[c.document_id] AS path
    FROM candidates c

    UNION ALL

    SELECT e.dst_document_id, target.chunk_index, target.content, target.version,
           w.dist + {GRAPH_DISTANCE_PENALTY},
           w.document_id, e.kind, w.depth + 1, w.path || e.dst_document_id
    FROM walk w
    JOIN document_edges e ON e.src_document_id = w.document_id
    JOIN documents d ON d.id = e.dst_document_id
    JOIN LATERAL (
        SELECT target.chunk_index, target.content, target.version
        FROM document_chunks target
        WHERE target.document_id = e.dst_document_id
          AND (e.dst_chunk_index IS NULL OR target.chunk_index = e.dst_chunk_index)
        ORDER BY target.embedding <=> %(qvec)s::vector
        LIMIT 1
    ) target ON true
    WHERE w.depth < {GRAPH_MAX_DEPTH}
      AND NOT e.dst_document_id = ANY(w.path)
      AND NOT EXISTS (
          SELECT 1 FROM direct_documents direct
          WHERE direct.document_id = e.dst_document_id
      )
      AND (%(tags)s::text[] IS NULL OR d.tags && %(tags)s)
      AND (%(ctype)s::text IS NULL OR d.content_type = %(ctype)s)
      AND {VISIBLE_TO_USER}
),
expanded AS (
    SELECT document_id, chunk_index, content, version, dist,
           via_document_id, via_kind, depth
    FROM walk

    UNION ALL

    SELECT c.document_id, c.chunk_index, previous.content, previous.version,
           c.dist + {GRAPH_DISTANCE_PENALTY}, c.document_id, 'revision', 1
    FROM candidates c
    JOIN document_versions previous
      ON previous.document_id = c.document_id
     AND previous.version = c.version - 1
),
deduplicated AS (
    SELECT DISTINCT ON (
        document_id,
        CASE WHEN depth = 0 THEN -1 ELSE version END,
        CASE WHEN depth = 0 OR via_kind = 'revision' THEN -1 ELSE chunk_index END
    ) *
    FROM expanded
    ORDER BY document_id,
             CASE WHEN depth = 0 THEN -1 ELSE version END,
             CASE WHEN depth = 0 OR via_kind = 'revision' THEN -1 ELSE chunk_index END,
             CASE WHEN depth = 0 THEN 0 ELSE 1 END,
             dist, depth
),
selected AS (
    (SELECT * FROM deduplicated
     WHERE depth = 0
     ORDER BY dist, document_id, chunk_index
     LIMIT %(k)s)

    UNION ALL

    (SELECT * FROM deduplicated
     WHERE depth > 0
     ORDER BY dist,
              CASE via_kind
                  WHEN 'overlaps' THEN 0 WHEN 'related' THEN 1
                  WHEN 'revision' THEN 2 ELSE 3
              END,
              depth, document_id, chunk_index
     LIMIT %(k)s)
)
SELECT d.id, d.title, d.filename, d.tags, d.content_type,
       hit.chunk_index, hit.content, 1 - hit.dist AS score, hit.version,
       hit.via_document_id, hit.via_kind, hit.depth
FROM selected hit
JOIN documents d ON d.id = hit.document_id
ORDER BY CASE WHEN hit.depth = 0 THEN 0 ELSE 1 END,
         hit.dist,
         CASE hit.via_kind
             WHEN 'overlaps' THEN 0 WHEN 'related' THEN 1
             WHEN 'revision' THEN 2 ELSE 3
         END,
         hit.depth,
         hit.document_id, hit.chunk_index
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
class SearchVia:
    from_document_id: UUID
    kind: str
    depth: int


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
    via: SearchVia | None


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
            via=(
                SearchVia(from_document_id=row[9], kind=row[10], depth=row[11])
                if row[9] is not None
                else None
            ),
        )
        for row in rows
    ]
