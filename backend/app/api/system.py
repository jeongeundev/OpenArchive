from typing import Annotated

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.api.deps import Connection, get_embedding_provider
from app.embeddings.base import EmbeddingProvider

router = APIRouter(prefix="/api/system", tags=["system"])


class JobCounts(BaseModel):
    pending: int
    processing: int
    error: int


class SystemStatus(BaseModel):
    """운영 상태 응답. reconnect_events는 M5에서 재연결 추적을 구현할 때 채운다."""

    node_address: str | None
    node_port: int
    jobs: JobCounts
    inconsistent_documents: int
    embedding_provider: str
    reconnect_events: None = None


@router.get("/status", response_model=SystemStatus)
async def get_system_status(
    conn: Connection,
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SystemStatus:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        WITH job_counts AS (
          SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                 count(*) FILTER (WHERE status = 'processing') AS processing,
                 count(*) FILTER (WHERE status = 'error') AS error
          FROM embedding_jobs
        ), consistency AS (
          SELECT count(DISTINCT c.document_id) AS inconsistent_documents
          FROM document_chunks c
          JOIN documents d ON d.id = c.document_id
          WHERE c.version <> d.version
        )
        SELECT host(inet_server_addr()) AS node_address,
               inet_server_port() AS node_port,
               j.pending, j.processing, j.error,
               s.inconsistent_documents
        FROM job_counts j CROSS JOIN consistency s
        """
    )
    row = await cur.fetchone()
    return SystemStatus(
        node_address=row["node_address"],
        node_port=row["node_port"],
        jobs=JobCounts(
            pending=row["pending"],
            processing=row["processing"],
            error=row["error"],
        ),
        inconsistent_documents=row["inconsistent_documents"],
        embedding_provider=provider.name,
    )
