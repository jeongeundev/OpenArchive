from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Connection, optional_user_id
from app.api.schemas import ClustersResponse
from app.services.clusters import get_clusters

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


@router.get("", response_model=ClustersResponse)
async def clusters(
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
) -> ClustersResponse:
    return ClustersResponse.model_validate(await get_clusters(conn, user_id=user_id))
