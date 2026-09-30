from typing import Annotated

from fastapi import APIRouter, Depends

from openarchive.api.deps import Connection, require_user_id
from openarchive.api.schemas import ClustersResponse
from openarchive.services.clusters import get_clusters

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


@router.get("", response_model=ClustersResponse)
async def clusters(
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> ClustersResponse:
    return ClustersResponse.model_validate(await get_clusters(conn, user_id=user_id))
