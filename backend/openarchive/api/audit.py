from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from openarchive.api.deps import Connection, require_admin
from openarchive.api.schemas import AuditEntry, AuditPage
from openarchive.services import audit as service

# 동작 목록은 서비스 한 곳에서만 관리한다. 모르는 값은 422다.
AuditAction = Literal[service.AUDIT_ACTIONS]

# 감사 로그 조회는 관리자·세션 전용이다 (ADR-034 결정 6, ADR-055 결정 7)
router = APIRouter(
    prefix="/api/admin/audit",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=AuditPage)
async def list_audit(
    conn: Connection,
    actor: str | None = None,
    action: AuditAction | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: int | None = None,
) -> AuditPage:
    rows = await service.list_audit(
        conn, actor=actor, action=action, limit=limit, before_id=before_id
    )
    items = [AuditEntry.model_validate(row) for row in rows]
    next_before_id = items[-1].id if len(items) == limit else None
    return AuditPage(items=items, next_before_id=next_before_id)
