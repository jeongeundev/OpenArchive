from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from openarchive.api.deps import Connection, require_admin
from openarchive.api.schemas import AdminShareSummary
from openarchive.services import shares as service
from openarchive.services.auth import TokenNotFound

router = APIRouter(
    prefix="/api/admin/shares", tags=["admin"], dependencies=[Depends(require_admin)]
)


@router.get("", response_model=list[AdminShareSummary])
async def list_shares(conn: Connection) -> list[AdminShareSummary]:
    return [
        AdminShareSummary.model_validate(row)
        for row in await service.list_all_shares(conn)
    ]


@router.delete("/{share_id}/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(share_id: UUID, token_id: UUID, conn: Connection) -> Response:
    try:
        await service.admin_revoke_share_token(conn, share_id, token_id)
    except TokenNotFound as error:
        raise HTTPException(status_code=404, detail="토큰을 찾을 수 없습니다.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
