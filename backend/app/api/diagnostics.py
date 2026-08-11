from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Connection, optional_user_id
from app.api.schemas import DiagnosticsResponse
from app.services.diagnostics import get_diagnostics

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("", response_model=DiagnosticsResponse)
async def diagnostics(
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
) -> DiagnosticsResponse:
    return DiagnosticsResponse.model_validate(
        await get_diagnostics(conn, user_id=user_id)
    )
