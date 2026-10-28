# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from openarchive.api.deps import Connection, require_admin
from openarchive.api.schemas import AuditCounts, AuditEntry, AuditPage
from openarchive.services import audit as service

# 동작 목록은 서비스 한 곳에서만 관리한다. 모르는 값은 422다.
AuditAction = Literal[service.AUDIT_ACTIONS]

# 감사 로그 조회는 관리자·세션 전용이다 (ADR-034 결정 6, ADR-055 결정 7)
router = APIRouter(
    prefix="/api/admin/audit",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/counts", response_model=AuditCounts)
async def count_audit_actions(
    conn: Connection,
    actor: str | None = None,
    since: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditCounts:
    actions = await service.count_audit_actions(conn, actor=actor, since=since, q=q)
    return AuditCounts(total=sum(actions.values()), actions=actions)


@router.get("", response_model=AuditPage)
async def list_audit(
    conn: Connection,
    actor: str | None = None,
    action: AuditAction | None = None,
    since: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: int | None = None,
    format: Literal["json", "csv"] = "json",
) -> AuditPage | Response:
    """CSV 파일명의 시각은 UTC다."""
    if format == "csv":
        rows = await service.list_audit(conn, actor=actor, action=action, since=since, q=q, limit=None)
        filename = datetime.now(UTC).strftime("audit-%Y%m%d-%H%M%S.csv")
        return Response(
            content=service.render_audit_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    rows = await service.list_audit(
        conn, actor=actor, action=action, since=since, q=q, limit=limit, before_id=before_id
    )
    items = [AuditEntry.model_validate(row) for row in rows]
    next_before_id = items[-1].id if len(items) == limit else None
    return AuditPage(items=items, next_before_id=next_before_id)
