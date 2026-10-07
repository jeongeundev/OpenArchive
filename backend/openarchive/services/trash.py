"""휴지통은 데이터를 보존하고, 소유자만 복원·영구 삭제한다 (ADR-060)."""

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services import audit, documents


async def trash_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str
) -> None:
    await documents._load_for_write(conn, document_id, user_id)
    # 위 확인과 이 UPDATE 사이에 다른 요청이 먼저 옮겼으면 0행이 되어 없는 문서로 답한다.
    cur = await conn.execute(
        "UPDATE documents SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
        (document_id,),
    )
    if cur.rowcount == 0:
        raise documents.DocumentNotFound


async def list_trash(
    conn: psycopg.AsyncConnection, *, user_id: str, retention_days: int
) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, title, deleted_at,
                   deleted_at + %s * interval '1 day' AS purge_at
            FROM documents
            WHERE owner_id = %s AND deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, id
            """,
            (retention_days, user_id),
        )
        return await cur.fetchall()


async def restore_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str
) -> dict:
    async with conn.transaction():
        cur = await conn.execute(
            "UPDATE documents SET deleted_at = NULL "
            "WHERE id = %s AND owner_id = %s AND deleted_at IS NOT NULL",
            (document_id, user_id),
        )
        if cur.rowcount == 0:
            raise documents.DocumentNotFound
        return await documents.get_document(conn, document_id, user_id=user_id)


async def purge_document(
    conn: psycopg.AsyncConnection, document_id: UUID, *, user_id: str
) -> None:
    cur = await conn.execute(
        "DELETE FROM documents WHERE id = %s AND owner_id = %s",
        (document_id, user_id),
    )
    if cur.rowcount == 0:
        raise documents.DocumentNotFound


async def purge_expired(conn: psycopg.AsyncConnection, *, retention_days: int) -> int:
    async with conn.transaction():
        await audit.set_actor(conn, actor=None, via="worker")
        cur = await conn.execute(
            "DELETE FROM documents WHERE deleted_at < now() - %s * interval '1 day'",
            (retention_days,),
        )
        return cur.rowcount
