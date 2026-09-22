from collections.abc import Callable
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
  WHERE kind = 'embed'
), consistency AS (
  SELECT count(DISTINCT c.document_id) AS inconsistent_documents
  FROM document_chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.version <> d.version
), stale_edges AS (
  SELECT count(DISTINCT document_id) AS stale_edge_documents
  FROM embedding_jobs
  WHERE kind = 'edges' AND status <> 'done'
)
SELECT host(inet_server_addr()) AS node_address,
       inet_server_port() AS node_port,
       j.pending, j.processing, j.recovery_pending, j.error,
       j.last_job_finished_at,
       s.inconsistent_documents,
       e.stale_edge_documents
FROM job_counts j CROSS JOIN consistency s CROSS JOIN stale_edges e
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
    stale_edge_documents: int
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
    # 정합성 카운터는 청크 수가 아니라 어긋난 문서 수를 센다. 관계 카운터도 문서 수이며,
    # 세는 근거는 `document_edges` 행 수가 아니라 관계 잡의 상태다 — 이웃이 없어 edge가
    # 0행인 문서와 아직 판정하지 않은 문서가 행 수로는 구분되지 않는다.
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
        stale_edge_documents=row["stale_edge_documents"],
        embedding_provider=embedding_provider,
    )


async def rebuild_all_edges(
    conn: psycopg.AsyncConnection,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """ready 문서 전부의 관계를 다시 계산하고 처리한 문서 수를 돌려준다.

    진행 중인 트랜잭션이 없는 연결을 받는다. 대상 조회와 문서별 재계산을 각각
    커밋해, 중간 실패가 이미 처리한 문서까지 되돌리거나 잠금을 오래 잡지 않는다.
    관계 교체의 원자성은 DB 함수가 지키고 진행 콜백은 커밋 뒤에 호출한다.
    """
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT id FROM documents WHERE embedding_status = 'ready' ORDER BY created_at, id"
        )
        documents = await cur.fetchall()
    total = len(documents)
    for done, (document_id,) in enumerate(documents, start=1):
        async with conn.transaction():
            await conn.execute("SELECT rebuild_document_edges(%s)", (document_id,))
        if on_progress is not None:
            on_progress(done, total)
    return total
