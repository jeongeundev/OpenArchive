from typing import Annotated

from fastapi import APIRouter, Depends

from openarchive.api.deps import Connection, get_embedding_provider, require_user_id
from openarchive.api.schemas import SystemStatus
from openarchive.config import get_settings
from openarchive.embeddings.base import EmbeddingProvider
from openarchive.services import system as service

router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_user_id)],
)


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SystemStatus:
    result = await service.get_system_status(
        conn,
        job_lease_seconds=get_settings().job_lease_seconds,
        embedding_provider=provider.name,
    )
    return SystemStatus.model_validate(result)
