"""정형 필터와 벡터 유사도를 한 SQL로 결합하는 검색 서비스."""

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

import psycopg

from openarchive.embeddings.base import EmbeddingProvider
from openarchive.services.chunking import chunk_text
from openarchive.services.visibility import VISIBLE_TO_USER
from openarchive.vectors import to_pgvector_literal

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
EXCERPT_LENGTH = 300
GRAPH_MAX_DEPTH = 2
# 코사인 거리는 최대 2다. 한 단계마다 그 범위만큼 벌려 직접 결과가 관계 확장보다
# 항상 앞서게 하고, 동시에 기존 `score = 1 - dist` 정렬 의미를 유지한다.
GRAPH_DISTANCE_PENALTY = 2.0
# 관계 종류의 우선순위. 최종 정렬뿐 아니라 같은 문서·같은 발췌로 수렴한 행을 하나로
# 접을 때도 이 순서로 남긴다 — 거리·깊이까지 같은 동점(문서 단위 overlaps와 위키링크
# refers가 같은 경유 문서에서 닿는 경우)이 실제로 생기며, 접는 규칙에 이 축이 빠지면
# 어느 쪽이 남는지가 정렬 구현의 물리 행 순서에 달린다 (ADR-011 보강 6).
VIA_KIND_PRIORITY = """CASE {alias}via_kind
        WHEN 'overlaps' THEN 0 WHEN 'related' THEN 1
        WHEN 'refers' THEN 2 WHEN 'revision' THEN 3 ELSE 4
    END"""

# 제목 우선순위는 이미 권한·필터를 통과한 직접 벡터 후보에만 적용한다.
# 발행 판본을 내부 텍스트 버전이나 날짜 없는 제목으로 추정하지 않는다.
TITLE_PRIORITY = """CASE WHEN
    (%(query)s = d.title OR (
        %(edition)s::text IS NOT NULL
        AND strpos(%(query)s, regexp_replace(d.title, ' [(][0-9]{4}판[)]$', '')) > 0
    ))
    AND (%(edition)s::text IS NULL OR
         substring(d.title FROM ' [(]([0-9]{4})판[)]$') = %(edition)s)
    THEN 0 ELSE 1 END"""

NUMBER_TITLE_PRIORITY = """CASE WHEN %(identifier)s::text IS NOT NULL
    AND strpos(%(query)s, regexp_replace(d.title, ' [(][0-9]{4}판[)]$', '')) > 0
    THEN 0 ELSE 1 END"""

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
resolved_links AS (
    SELECT l.src_document_id, d.id AS dst_document_id,
           'refers'::text AS kind,
           NULL::int AS dst_chunk_index
    FROM document_links l
    JOIN documents d
      ON d.title = l.target_title
     AND {VISIBLE_TO_USER}
),
traversal_edges AS (
    SELECT e.src_document_id, e.dst_document_id, e.kind, e.dst_chunk_index
    FROM document_edges e
    UNION ALL
    -- 역방향. 계산 주체가 어느 쪽이든 관계는 무방향이다 (ADR-029 개정 — 저장은 단방향이 된다).
    SELECT e.dst_document_id, e.src_document_id, e.kind, e.src_chunk_index
    FROM document_edges e
    UNION ALL
    SELECT src_document_id, dst_document_id, kind, dst_chunk_index
    FROM resolved_links
),
walk_ids AS (
    -- 순회는 문서 id·거리·경로만 나른다. 발췌(청크 본문)는 여기서 고르지 않는다 —
    -- 촘촘한 그래프에서 깊이 2 순회 행은 만 단위인데 그 행마다 청크 정렬을 돌리면
    -- 그것이 검색 비용의 전부가 된다 (ADR-011 보강 6: 13,332회 → 204회, 3.5~11.6초 → 0.35~0.81초).
    SELECT c.document_id, c.dist,
           NULL::uuid AS via_document_id, NULL::text AS via_kind, 0 AS depth,
           ARRAY[c.document_id] AS path, NULL::int AS target_chunk_index
    FROM candidates c

    UNION ALL

    SELECT e.dst_document_id, w.dist + {GRAPH_DISTANCE_PENALTY},
           w.document_id, e.kind, w.depth + 1, w.path || e.dst_document_id,
           e.dst_chunk_index
    FROM walk_ids w
    JOIN traversal_edges e ON e.src_document_id = w.document_id
    JOIN documents d ON d.id = e.dst_document_id
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
walk_targets AS (
    -- 같은 문서·같은 대상 청크(NULL은 -1로 접는다)에 여러 경로로 닿으면 가장 가까운
    -- 경로 하나만 남긴다. 아래 deduplicated가 실제 chunk_index로 한 번 더 접으므로
    -- NULL 대상과 명시 대상이 같은 청크로 수렴해도 결과는 이전과 같다.
    SELECT DISTINCT ON (document_id, COALESCE(target_chunk_index, -1))
           document_id, dist, via_document_id, via_kind, depth, target_chunk_index
    FROM walk_ids
    WHERE depth > 0
    ORDER BY document_id, COALESCE(target_chunk_index, -1), dist,
             {VIA_KIND_PRIORITY.format(alias='')}, depth
),
walk AS (
    SELECT c.document_id, c.chunk_index, c.content, c.version, c.dist,
           NULL::uuid AS via_document_id, NULL::text AS via_kind, 0 AS depth
    FROM candidates c

    UNION ALL

    SELECT w.document_id, target.chunk_index, target.content, target.version, w.dist,
           w.via_document_id, w.via_kind, w.depth
    FROM walk_targets w
    JOIN LATERAL (
        SELECT target.chunk_index, target.content, target.version
        FROM document_chunks target
        WHERE target.document_id = w.document_id
          AND (w.target_chunk_index IS NULL OR target.chunk_index = w.target_chunk_index)
        ORDER BY target.embedding <=> %(qvec)s::vector
        LIMIT 1
    ) target ON true
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
             dist, {VIA_KIND_PRIORITY.format(alias='')}, depth
),
selected AS (
    (SELECT * FROM deduplicated
     WHERE depth = 0
     ORDER BY (SELECT {TITLE_PRIORITY} FROM documents d
                      WHERE d.id = deduplicated.document_id),
              (SELECT {NUMBER_TITLE_PRIORITY} FROM documents d
               WHERE d.id = deduplicated.document_id),
              dist, document_id, chunk_index
     LIMIT %(k)s)

    UNION ALL

    (SELECT * FROM deduplicated
     WHERE depth > 0
     ORDER BY dist,
              {VIA_KIND_PRIORITY.format(alias='')},
              depth, document_id, chunk_index
     LIMIT %(k)s)
)
SELECT d.id, d.title, d.filename, d.tags, d.content_type,
       COALESCE(excerpt.chunk_index, hit.chunk_index),
       COALESCE(excerpt.content, hit.content), 1 - hit.dist AS score, hit.version,
       hit.via_document_id, hit.via_kind, hit.depth, preview_chunks.items
FROM selected hit
JOIN documents d ON d.id = hit.document_id
LEFT JOIN LATERAL (
    SELECT matched.chunk_index,
           (SELECT string_agg(context.content, E'\n\n' ORDER BY context.chunk_index)
            FROM document_chunks context
            WHERE context.document_id = hit.document_id AND context.version = hit.version
              AND context.chunk_index BETWEEN matched.chunk_index - 1 AND matched.chunk_index + 1
           ) AS content
    FROM document_chunks matched
    WHERE hit.depth = 0 AND %(identifier)s::text IS NOT NULL
      AND matched.document_id = hit.document_id AND matched.version = hit.version
      AND matched.content ~ %(identifier)s
    ORDER BY matched.embedding <=> %(qvec)s::vector, matched.chunk_index
    LIMIT 1
) excerpt ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_array(c.chunk_index, c.content)
                     ORDER BY c.chunk_index) AS items
    FROM document_chunks c
    WHERE hit.depth = 0
      AND c.document_id = hit.document_id AND c.version = hit.version
      AND c.chunk_index BETWEEN COALESCE(excerpt.chunk_index, hit.chunk_index) - 1
                            AND COALESCE(excerpt.chunk_index, hit.chunk_index) + 1
) preview_chunks ON true
ORDER BY CASE WHEN hit.depth = 0 THEN 0 ELSE 1 END,
         CASE WHEN hit.depth = 0 THEN {TITLE_PRIORITY} ELSE 1 END,
         CASE WHEN hit.depth = 0 THEN {NUMBER_TITLE_PRIORITY} ELSE 1 END,
         hit.dist,
         {VIA_KIND_PRIORITY.format(alias='hit.')},
         hit.depth,
         hit.document_id, hit.chunk_index
"""


async def apply_vector_search_settings(conn: psycopg.AsyncConnection) -> None:
    """벡터 정렬 트랜잭션에 필요한 설정을 건다 (ADR-011 보강 4·5, ADR-044).

    `ef_search`와 `random_page_cost`는 항상 짝으로 걸어야 한다 — `random_page_cost`만
    빠져도 플래너가 HNSW를 아예 고르지 않는다. JIT는 끈다 — 열람 술어의 부여 서브플랜이
    추정 비용을 `jit_above_cost` 위로 올려, JIT가 있는 PostgreSQL에서는 몇 ms짜리 검색에
    컴파일 수십 ms가 붙는다. 호출부마다 반복하면 하나를 빠뜨려도 에러 없이 느려질 뿐이라
    한 곳에 모은다. SET LOCAL은 바인딩할 수 없고, 값은 사용자 입력이 아닌 모듈 상수다.
    """
    await conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
    await conn.execute("SET LOCAL random_page_cost = 1.1")
    await conn.execute("SET LOCAL jit = off")


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
    preview: str | None = None


def _identifier_pattern(query: str) -> str | None:
    """명시한 번호 하나만 발췌에 반영한다. 비교 질문은 기존 선택을 유지한다."""
    identifiers = set(re.findall(
        r"(?<![\w-])ADR-[0-9]+(?![0-9])|제\s*[0-9]+\s*조(?:\s*의\s*[0-9]+)?",
        query, re.IGNORECASE,
    ))
    identifiers = {re.sub(r"\s+", "", value).upper() for value in identifiers}
    if len(identifiers) != 1:
        return None
    identifier = next(iter(identifiers))
    if identifier.startswith("ADR-"):
        return rf"(?i)(?<![[:alnum:]_-]){identifier}(?![0-9])"
    # 조의 하위 번호는 별도 설계 전까지 일반 조항으로 해석하지 않는다.
    if "의" in identifier:
        return None
    number = identifier[1:-1]
    return rf"제[[:space:]]*{number}[[:space:]]*조(?![[:space:]]*의[[:space:]]*[0-9])"


def _preview_index(query: str, passages: list[str]) -> int | None:
    """질의의 문자 쌍이 드러난 대목을 고른다. 일치가 없으면 벡터 발췌를 유지한다.

    한국어 조사 차이도 일부 허용하도록 단어 안의 두 글자를 비교한다. 여러 대목에
    반복되는 문자 쌍은 덜 반영하고, 길이가 긴 대목의 우연한 일치를 보정한다.
    의미 이해나 답변 정확도 점수는 아니며 문서 순위·유사도에는 쓰지 않는다.
    """
    def grams(text: str) -> set[str]:
        return {
            word[i:i + 2]
            for word in re.findall(r"[가-힣]+", text)
            for i in range(len(word) - 1)
        } | set(re.findall(r"[a-z0-9]+", text.casefold()))

    # 조사·어미가 질의어로 가중되지 않게 한국어 질의 단어의 첫 두 글자만 쓴다.
    # 본문에서는 단어 안 어디든 비교한다 (예: 재수정 ↔ 수정하면).
    query_grams = {
        word[:2] for word in re.findall(r"[가-힣]+", query) if len(word) >= 2
    } | set(re.findall(r"[a-z0-9]+", query.casefold()))
    passage_grams = [grams(p) for p in passages]
    frequency = Counter(g for gs in passage_grams for g in gs)
    scores = [
        sum(1 / frequency[g] for g in gs & query_grams) / max(1, len(gs)) ** 0.5
        for gs in passage_grams
    ]
    winner = max(range(len(scores)), key=scores.__getitem__)
    return winner if scores[winner] > 0 else None


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
    editions = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?=\s*(?:판|년판))", query))
    params = {
        "query": query,
        "identifier": _identifier_pattern(query),
        "edition": next(iter(editions)) if len(editions) == 1 else None,
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

    # 이미 선택된 문서·버전의 주변 청크만 사용한다. 열람 범위와 문서 순위는 SQL이 결정한다.
    identifier = params["identifier"]
    identifier_regex = re.compile(
        identifier.replace("[[:alnum:]_-]", r"[\w-]").replace("[[:space:]]", r"\s")
    ) if identifier else None
    excerpts = {}
    for index, row in enumerate(rows):
        if identifier_regex is not None:
            for chunk_index, content in row[12] or []:
                if chunk_index == row[5] and (match := identifier_regex.search(content)):
                    excerpts[index] = (chunk_index, content[match.start():match.start() + EXCERPT_LENGTH])
                    break
            continue
        options = []
        for chunk_index, content in row[12] or []:
            options.extend((chunk_index, passage) for passage in
                           chunk_text(content, max_chars=EXCERPT_LENGTH, overlap=0))
        if options:
            winner = _preview_index(query, [p for _, p in options])
            if winner is not None:
                chunk_index, passage = options[winner]
                excerpts[index] = (chunk_index, passage)

    return [
        SearchHit(
            document_id=row[0],
            title=row[1],
            filename=row[2],
            tags=row[3],
            content_type=row[4],
            chunk_index=excerpts[index][0] if index in excerpts else row[5],
            content=row[6],
            preview=excerpts[index][1] if index in excerpts else None,
            score=float(row[7]),
            based_on_version=row[8],
            via=(
                SearchVia(from_document_id=row[9], kind=row[10], depth=row[11])
                if row[9] is not None
                else None
            ),
        )
        for index, row in enumerate(rows)
    ]
