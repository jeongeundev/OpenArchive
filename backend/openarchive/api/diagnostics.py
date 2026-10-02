from typing import Annotated

from fastapi import APIRouter, Depends

from openarchive.api.deps import Connection, require_reader
from openarchive.api.schemas import DiagnosticsResponse
from openarchive.services.diagnostics import get_diagnostics

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("", response_model=DiagnosticsResponse)
async def diagnostics(
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> DiagnosticsResponse:
    return DiagnosticsResponse.model_validate(
        await get_diagnostics(conn, user_id=user_id)
    )
