"""감사 행위자는 트랜잭션 범위로만 DB에 전달한다."""

from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

ACTOR_VIA: tuple[str, ...] = ("session", "token", "mcp", "cli", "share", "worker")
AUDIT_ACTIONS: tuple[str, ...] = (
    "document_created",
    "text_updated",
    "document_deleted",
    "access_changed",
    "group_member_changed",
    "original_replaced",
    "original_downloaded",
    "folder_access_changed",
)

# 같은 트랜잭션의 행은 occurred_at이 같으므로 정렬·커서는 id로 한다.
# 관리자에게 대상 문서 제목만 보이는 예외라 열람 술어를 걸지 않는다 (ADR-055 결정 8).
LIST_AUDIT_SQL = """
SELECT id, occurred_at, action, actor, actor_via, db_role,
       document_id, document_title, detail
FROM audit_log
WHERE (%(actor)s::text IS NULL OR actor = %(actor)s)
  AND (%(action)s::text IS NULL OR action = %(action)s)
  AND (%(before_id)s::bigint IS NULL OR id < %(before_id)s)
ORDER BY id DESC
LIMIT %(limit)s
"""


async def set_actor(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None,
    via: str,
    share_id: UUID | None = None,
) -> None:
    """이 트랜잭션의 감사 행위자를 DB에 넘긴다 (ADR-055 결정 3).

    HA 실측에서 세션 SET은 다음 클라이언트로 75/100 누수되어 SET LOCAL만 쓴다.
    """
    if via not in ACTOR_VIA:
        raise ValueError("허용되지 않은 감사 경로입니다.")
    if (via == "share") != (share_id is not None):
        raise ValueError("공유 경로와 share_id를 함께 지정해야 합니다.")
    if conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError("감사 행위자 설정은 트랜잭션 안에서 불러야 한다.")
    for name, value in (
        ("openarchive.actor_id", actor or ""),
        ("openarchive.actor_via", via),
        ("openarchive.share_id", str(share_id) if share_id is not None else ""),
    ):
        await conn.execute("SELECT set_config(%s, %s, true)", (name, value))


async def list_audit(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None = None,
    action: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict]:
    """감사 기록을 최신순으로 돌려준다. 조회 자체는 기록하지 않는다 (ADR-055 결정 6)."""
    if action is not None and action not in AUDIT_ACTIONS:
        raise ValueError("알 수 없는 감사 동작입니다.")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            LIST_AUDIT_SQL,
            {"actor": actor, "action": action, "limit": limit, "before_id": before_id},
        )
        return await cur.fetchall()
