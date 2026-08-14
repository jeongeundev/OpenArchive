from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Connection, get_embedding_provider
from app.api.schemas import SystemStatus
from app.config import get_settings
from app.embeddings.base import EmbeddingProvider
from app.services import system as service

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SystemStatus:
    result = await service.get_system_status(
        conn,
        zombie_timeout_minutes=get_settings().zombie_timeout_minutes,
        embedding_provider=provider.name,
    )
    return SystemStatus.model_validate(result)
