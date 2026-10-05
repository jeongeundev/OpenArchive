"""감사 행위자는 트랜잭션 범위로만 DB에 전달한다."""

from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

ACTOR_VIA: tuple[str, ...] = ("session", "token", "mcp", "cli", "share", "worker")


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
