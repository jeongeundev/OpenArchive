# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""감사 행위자는 트랜잭션 범위로만 DB에 전달한다."""

import csv
import io
import json
from datetime import datetime
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
    "original_previewed",
    "folder_access_changed",
    "document_trashed",
    "document_restored",
    "owner_changed",
    "share_changed",
    "group_changed",
    "user_changed",
    "password_changed",
    "folder_changed",
    "token_changed",
)

# 같은 트랜잭션의 행은 occurred_at이 같으므로 정렬·커서는 id로 한다.
# 관리자에게 대상 문서 제목만 보이는 예외라 열람 술어를 걸지 않는다 (ADR-055 결정 8).
AUDIT_FILTER_SQL = """
(%(actor)s::text IS NULL OR actor = %(actor)s)
AND (%(since)s::timestamptz IS NULL OR occurred_at >= %(since)s)
AND (%(q)s::text IS NULL OR document_title ILIKE %(q)s
     OR detail->>'folder_name' ILIKE %(q)s)
"""

LIST_AUDIT_SQL = f"""
SELECT id, occurred_at, action, actor, actor_via, db_role,
       document_id, document_title, detail
FROM audit_log
WHERE {AUDIT_FILTER_SQL}
  AND (%(action)s::text IS NULL OR action = %(action)s)
  AND (%(before_id)s::bigint IS NULL OR id < %(before_id)s)
ORDER BY id DESC
LIMIT %(limit)s
"""

# 문서 단위 활동 기록은 그 문서를 보는 사람에게 연다 (ADR-055 결정 7 개정).
# 열람 범위·공유·원본 열람 기록은 누가 무엇을 받았는지 드러내므로 소유자에게만 보인다.
DOCUMENT_ACTIVITY_PUBLIC_ACTIONS: tuple[str, ...] = (
    "document_created",
    "text_updated",
    "original_replaced",
    "document_trashed",
    "document_restored",
    "owner_changed",
)
DOCUMENT_ACTIVITY_OWNER_ACTIONS: tuple[str, ...] = (
    "access_changed",
    "share_changed",
    "original_downloaded",
    "original_previewed",
)

LIST_DOCUMENT_ACTIVITY_SQL = """
SELECT id, occurred_at, action, actor, actor_via, detail
FROM audit_log
WHERE document_id = %(id)s
  AND action = ANY(%(actions)s)
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


async def mark_text_from_extraction(conn: psycopg.AsyncConnection) -> None:
    """재추출이 다시 만든 텍스트의 버전 작성자를 워커로 기록한다. 감사 행위자는 그대로 둔다.

    작성자는 행동 기준이다 — 업로드·원본 교체·편집·복원은 요청한 사람, OCR 반영·재추출은 워커다
    (ADR-061 결정 6). 그래서 이 표시는 재추출 경로에만 건다.
    """
    if conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE:
        raise RuntimeError("추출 출처 설정은 트랜잭션 안에서 불러야 한다.")
    await conn.execute("SELECT set_config('openarchive.text_source', 'extraction', true)")


async def list_audit(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    q: str | None = None,
    limit: int | None = 50,
    before_id: int | None = None,
) -> list[dict]:
    """감사 기록을 최신순으로 돌려준다. 조회 자체는 기록하지 않는다 (ADR-055 결정 6)."""
    if action is not None and action not in AUDIT_ACTIONS:
        raise ValueError("알 수 없는 감사 동작입니다.")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            LIST_AUDIT_SQL,
            {**_audit_filter_params(actor, since, q),
             "action": action, "limit": limit, "before_id": before_id},
        )
        return await cur.fetchall()


def _audit_filter_params(actor, since, q) -> dict:
    term = q.strip() if q else ""
    pattern = None
    if term:
        for char in ("\\", "%", "_"):
            term = term.replace(char, "\\" + char)
        pattern = "%" + term + "%"
    return {"actor": actor, "since": since, "q": pattern}


async def count_audit_actions(
    conn: psycopg.AsyncConnection,
    *,
    actor: str | None = None,
    since: datetime | None = None,
    q: str | None = None,
) -> dict[str, int]:
    """목록과 같은 조건의 동작별 건수를 SQL 한 번으로 센다."""
    cur = await conn.execute(
        f"SELECT action, count(*) FROM audit_log WHERE {AUDIT_FILTER_SQL} GROUP BY action",
        _audit_filter_params(actor, since, q),
    )
    return dict(await cur.fetchall())


async def list_document_activity(
    conn: psycopg.AsyncConnection,
    document_id: UUID,
    *,
    user_id: str | None,
    limit: int = 50,
    before_id: int | None = None,
) -> list[dict]:
    """그 문서의 활동 기록을 최신순으로 돌려준다. 볼 수 없는 문서는 DocumentNotFound다."""
    # documents가 이 모듈을 import하므로 순환을 피해 호출 시점에 가져온다.
    from openarchive.services.documents import DocumentNotFound, ensure_visible

    if user_id is None:
        raise DocumentNotFound
    await ensure_visible(conn, document_id, user_id=user_id)
    cur = await conn.execute(
        "SELECT owner_id = %s FROM documents WHERE id = %s", (user_id, document_id)
    )
    is_owner = (await cur.fetchone())[0]
    actions = DOCUMENT_ACTIVITY_PUBLIC_ACTIONS
    if is_owner:
        actions += DOCUMENT_ACTIVITY_OWNER_ACTIONS
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            LIST_DOCUMENT_ACTIVITY_SQL,
            {"id": document_id, "actions": list(actions), "limit": limit, "before_id": before_id},
        )
        return await cur.fetchall()


def render_audit_csv(rows: list[dict]) -> str:
    """원시 감사 값을 BOM이 있는 CSV로 만들고 셀의 수식 해석을 막는다."""
    columns = (
        "id", "occurred_at", "actor", "actor_via", "db_role", "action",
        "document_id", "document_title", "detail",
    )
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        cells = []
        for column in columns:
            value = row[column]
            if value is None:
                cell = ""
            elif column == "occurred_at":
                cell = value.isoformat()
            elif column == "detail":
                cell = json.dumps(value, ensure_ascii=False)
            else:
                cell = str(value)
            if cell.startswith(("=", "+", "-", "@", "\t", "\r")):
                cell = "'" + cell
            cells.append(cell)
        writer.writerow(cells)
    return output.getvalue()
