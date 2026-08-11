from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.api.deps import Connection, get_embedding_provider
from app.config import get_settings
from app.embeddings.base import EmbeddingProvider

router = APIRouter(prefix="/api/system", tags=["system"])


class JobCounts(BaseModel):
    pending: int
    processing: int
    recovery_pending: int
    error: int


class SystemStatus(BaseModel):
    """운영 상태 응답."""

    node_address: str | None
    node_port: int
    jobs: JobCounts
    zombie_timeout_minutes: int
    last_job_finished_at: datetime | None
    inconsistent_documents: int
    embedding_provider: str


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SystemStatus:
    zombie_timeout_minutes = get_settings().zombie_timeout_minutes
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        WITH job_counts AS (
          SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                 count(*) FILTER (WHERE status = 'processing') AS processing,
                 count(*) FILTER (
                   WHERE status = 'processing'
                     AND started_at < now() - make_interval(mins => %(zombie_timeout_minutes)s)
                 ) AS recovery_pending,
                 count(*) FILTER (WHERE status = 'error') AS error,
                 max(finished_at) AS last_job_finished_at
          FROM embedding_jobs
        ), consistency AS (
          SELECT count(DISTINCT c.document_id) AS inconsistent_documents
          FROM document_chunks c
          JOIN documents d ON d.id = c.document_id
          WHERE c.version <> d.version
        )
        SELECT host(inet_server_addr()) AS node_address,
               inet_server_port() AS node_port,
               j.pending, j.processing, j.recovery_pending, j.error,
               j.last_job_finished_at,
               s.inconsistent_documents
        FROM job_counts j CROSS JOIN consistency s
        """,
        {"zombie_timeout_minutes": zombie_timeout_minutes},
    )
    row = await cur.fetchone()
    return SystemStatus(
        node_address=row["node_address"],
        node_port=row["node_port"],
        jobs=JobCounts(
            pending=row["pending"],
            processing=row["processing"],
            recovery_pending=row["recovery_pending"],
            error=row["error"],
        ),
        zombie_timeout_minutes=zombie_timeout_minutes,
        last_job_finished_at=row["last_job_finished_at"],
        inconsistent_documents=row["inconsistent_documents"],
        embedding_provider=provider.name,
    )
