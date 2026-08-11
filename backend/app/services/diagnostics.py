"""열람 가능한 문서 관계를 기준으로 정리 후보를 집계한다."""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.services.visibility import VISIBLE_TO_USER

ITEM_LIMIT = 10
# overlaps는 "자기 대목 중 상대 문서에서 최근접 이웃을 찾은 비율"이며 내용 동일성이 아니다.
# 이웃 판정이 순위 기반(절대 거리 임계 없음)이라 주제가 가까운 문서끼리는 모든 대목이 서로
# 최근접이 되어 비율이 1.0에 붙는다. 실 BGE-M3 63문서 280청크 실측에서 0.95 이상이 96쌍이었고
# 상위 쌍은 `PRD`↔`UI 디자인 가이드`, `ADR-020`↔`고가용성(HA) 전략`처럼 전부 "관련 있지만 다른
# 문서"였다. 청크 수 하한으로는 걸러지지 않는다 — 하한 4에서도 4청크·8청크인 위 쌍이 남는다
# (OPENSQL_RESEARCH §14). 그래서 임계를 신뢰도로 읽지 않고 목록의 이름과 화면 문구를 신호에
# 맞춘다: 이 목록은 정리 후보가 아니라 "여러 대목에서 만나는 문서"다. 확정 중복은
# content_hash가 일치하는 identical뿐이며 별도로 반환한다.
DUPLICATE_OVERLAP_RATIO = 0.95

ORPHANS_SQL = f"""
WITH visible_documents AS (
  SELECT d.id, d.title
  FROM documents d
  WHERE {VISIBLE_TO_USER}
), ranked AS (
  SELECT target.id, target.title, count(*) OVER () AS total
  FROM visible_documents target
  WHERE NOT EXISTS (
    SELECT 1
    FROM document_edges e
    JOIN visible_documents neighbor
      ON neighbor.id = CASE
        WHEN e.src_document_id = target.id THEN e.dst_document_id
        ELSE e.src_document_id
      END
    WHERE e.src_document_id = target.id OR e.dst_document_id = target.id
  )
  ORDER BY target.title, target.id
)
SELECT id, title, total FROM ranked LIMIT %(limit)s
"""

UNCATEGORIZED_SQL = f"""
WITH ranked AS (
  SELECT d.id, d.title, count(*) OVER () AS total
  FROM documents d
  WHERE {VISIBLE_TO_USER}
    AND cardinality(d.tags) = 0
  ORDER BY d.title, d.id
)
SELECT id, title, total FROM ranked LIMIT %(limit)s
"""

BROKEN_LINKS_SQL = f"""
WITH visible_sources AS (
  SELECT d.id, d.title
  FROM documents d
  WHERE {VISIBLE_TO_USER}
), ranked AS (
  SELECT src.id, src.title, l.target_title, count(*) OVER () AS total
  FROM document_links l
  JOIN visible_sources src ON src.id = l.src_document_id
  LEFT JOIN documents d
    ON d.title = l.target_title
   AND {VISIBLE_TO_USER}
  WHERE d.id IS NULL
  ORDER BY src.title, src.id, l.target_title
)
SELECT id, title, target_title, total FROM ranked LIMIT %(limit)s
"""

DUPLICATES_SQL = f"""
WITH visible_documents AS (
  SELECT d.id, d.title, d.content_hash
  FROM documents d
  WHERE {VISIBLE_TO_USER}
), identical AS (
  SELECT 'identical'::text AS match_type,
         a.id AS first_id, a.title AS first_title,
         b.id AS second_id, b.title AS second_title,
         NULL::real AS score
  FROM visible_documents a
  JOIN visible_documents b ON b.content_hash = a.content_hash AND a.id < b.id
), overlap_pairs AS (
  SELECT 'overlaps'::text AS match_type,
         a.id AS first_id, a.title AS first_title,
         b.id AS second_id, b.title AS second_title,
         max(e.score) AS score
  FROM document_edges e
  JOIN visible_documents a ON a.id = LEAST(e.src_document_id, e.dst_document_id)
  JOIN visible_documents b ON b.id = GREATEST(e.src_document_id, e.dst_document_id)
  WHERE e.kind = 'overlaps'
    AND e.score >= %(overlap_ratio)s::real
    AND a.content_hash <> b.content_hash
  GROUP BY a.id, a.title, b.id, b.title
), combined AS (
  SELECT * FROM identical
  UNION ALL
  SELECT * FROM overlap_pairs
), ranked AS (
  SELECT *, count(*) OVER (PARTITION BY match_type) AS total,
         row_number() OVER (
           PARTITION BY match_type
           ORDER BY score DESC NULLS LAST, first_title, second_title
         ) AS item_number
  FROM combined
)
SELECT match_type, first_id, first_title, second_id, second_title, score, total
FROM ranked
WHERE item_number <= %(limit)s
ORDER BY match_type, score DESC NULLS LAST, first_title, second_title
"""


@dataclass(frozen=True)
class DiagnosticDocument:
    document_id: UUID
    title: str


@dataclass(frozen=True)
class DiagnosticList:
    count: int
    items: list[DiagnosticDocument]


@dataclass(frozen=True)
class BrokenLink:
    source: DiagnosticDocument
    target_title: str


@dataclass(frozen=True)
class BrokenLinkList:
    count: int
    items: list[BrokenLink]


@dataclass(frozen=True)
class DuplicatePair:
    first: DiagnosticDocument
    second: DiagnosticDocument
    score: float | None


@dataclass(frozen=True)
class DuplicateList:
    count: int
    items: list[DuplicatePair]


@dataclass(frozen=True)
class DuplicateDiagnostics:
    identical: DuplicateList
    overlaps: DuplicateList


@dataclass(frozen=True)
class DiagnosticsResult:
    orphans: DiagnosticList
    duplicates: DuplicateDiagnostics
    uncategorized: DiagnosticList
    broken_links: BrokenLinkList


def _document_list(rows: list[tuple]) -> DiagnosticList:
    return DiagnosticList(
        count=rows[0][2] if rows else 0,
        items=[DiagnosticDocument(document_id=row[0], title=row[1]) for row in rows],
    )


async def get_diagnostics(
    conn: psycopg.AsyncConnection, *, user_id: str | None = None
) -> DiagnosticsResult:
    """사용자가 볼 수 있는 그래프만으로 고아·중복·미분류 후보를 반환한다."""
    params = {
        "user": user_id,
        "limit": ITEM_LIMIT,
        "overlap_ratio": DUPLICATE_OVERLAP_RATIO,
    }
    async with conn.transaction():
        orphan_rows = await (await conn.execute(ORPHANS_SQL, params)).fetchall()
        duplicate_rows = await (await conn.execute(DUPLICATES_SQL, params)).fetchall()
        uncategorized_rows = await (
            await conn.execute(UNCATEGORIZED_SQL, params)
        ).fetchall()
        broken_link_rows = await (
            await conn.execute(BROKEN_LINKS_SQL, params)
        ).fetchall()

    pairs: dict[str, list[DuplicatePair]] = {"identical": [], "overlaps": []}
    totals = {"identical": 0, "overlaps": 0}
    for row in duplicate_rows:
        match_type = row[0]
        totals[match_type] = row[6]
        if len(pairs[match_type]) < ITEM_LIMIT:
            pairs[match_type].append(
                DuplicatePair(
                    first=DiagnosticDocument(document_id=row[1], title=row[2]),
                    second=DiagnosticDocument(document_id=row[3], title=row[4]),
                    score=None if row[5] is None else float(row[5]),
                )
            )

    return DiagnosticsResult(
        orphans=_document_list(orphan_rows),
        duplicates=DuplicateDiagnostics(
            identical=DuplicateList(
                count=totals["identical"], items=pairs["identical"]
            ),
            overlaps=DuplicateList(
                count=totals["overlaps"], items=pairs["overlaps"]
            ),
        ),
        uncategorized=_document_list(uncategorized_rows),
        broken_links=BrokenLinkList(
            count=broken_link_rows[0][3] if broken_link_rows else 0,
            items=[
                BrokenLink(
                    source=DiagnosticDocument(document_id=row[0], title=row[1]),
                    target_title=row[2],
                )
                for row in broken_link_rows
            ],
        ),
    )
