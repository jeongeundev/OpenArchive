from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

SYSTEM_STATUS_SQL = """
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
"""


@dataclass(frozen=True)
class JobCounts:
    pending: int
    processing: int
    recovery_pending: int
    error: int


@dataclass(frozen=True)
class SystemStatusResult:
    node_address: str | None
    node_port: int
    jobs: JobCounts
    zombie_timeout_minutes: int
    last_job_finished_at: datetime | None
    inconsistent_documents: int
    embedding_provider: str


async def get_system_status(
    conn: psycopg.AsyncConnection,
    *,
    zombie_timeout_minutes: int,
    embedding_provider: str,
) -> SystemStatusResult:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        SYSTEM_STATUS_SQL,
        {"zombie_timeout_minutes": zombie_timeout_minutes},
    )
    row = await cur.fetchone()
    # 정합성 카운터는 청크 수가 아니라 어긋난 문서 수를 센다.
    return SystemStatusResult(
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
        embedding_provider=embedding_provider,
    )
