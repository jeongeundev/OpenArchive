"""위키링크를 조회하는 사용자의 열람 범위에서 해석한다."""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.services.visibility import VISIBLE_TO_USER

RESOLVE_LINKS_SQL = f"""
SELECT l.target_title, d.id
FROM document_links l
LEFT JOIN documents d
  ON d.title = l.target_title
 AND {VISIBLE_TO_USER}
WHERE l.src_document_id = %(id)s
ORDER BY l.target_title, d.created_at, d.id
"""

BACKLINKS_SQL = f"""
SELECT d.id, d.title
FROM documents target
JOIN document_links l ON l.target_title = target.title
JOIN documents d ON d.id = l.src_document_id
WHERE target.id = %(id)s
  AND {VISIBLE_TO_USER}
ORDER BY d.created_at, d.id
"""


@dataclass(frozen=True)
class ResolvedLink:
    title: str
    document_id: UUID | None


@dataclass(frozen=True)
class Backlink:
    document_id: UUID
    title: str


async def resolve_links(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
) -> list[ResolvedLink]:
    """문서가 낸 링크를 대상이 없거나 보이지 않아도 빠뜨리지 않고 반환한다."""
    rows = await (
        await conn.execute(
            RESOLVE_LINKS_SQL,
            {"id": document_id, "user": user_id},
        )
    ).fetchall()
    return [ResolvedLink(title=row[0], document_id=row[1]) for row in rows]


async def find_backlinks(
    conn: psycopg.AsyncConnection,
    *,
    document_id: UUID,
    user_id: str | None = None,
) -> list[Backlink]:
    """현재 문서 제목을 가리키는 열람 가능한 출발 문서를 반환한다."""
    rows = await (
        await conn.execute(
            BACKLINKS_SQL,
            {"id": document_id, "user": user_id},
        )
    ).fetchall()
    return [Backlink(document_id=row[0], title=row[1]) for row in rows]
