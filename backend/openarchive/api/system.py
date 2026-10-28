# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from openarchive.api.deps import Connection, get_embedding_provider, require_user_id
from openarchive.api.schemas import DbOutage, SystemStatus
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


@router.get("/outages", response_model=list[DbOutage])
async def get_db_outages(conn: Connection, limit: int = Query(default=20, ge=1, le=200)):
    from openarchive.services.db_outages import list_outages

    return await list_outages(conn, limit=limit)
