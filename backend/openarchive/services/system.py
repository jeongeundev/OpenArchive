import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from openarchive.services.audit import set_actor
from openarchive.services.documents import (
    DocumentNotFound,
    EmptyExtractedText,
    ExtractedTextTooLarge,
    ExtractionInProgress,
    VersionConflict,
    reextract_text,
)

SYSTEM_STATUS_SQL = """
WITH job_counts AS (
  SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
         count(*) FILTER (WHERE status = 'processing') AS processing,
         -- 스윕이 다음 주기에 회수할 잡 — sweep_zombies와 같은 판정이다 (ADR-050).
         count(*) FILTER (
           WHERE status = 'processing' AND lease_expires_at < now()
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
), extraction AS (
  -- 문서 수다. 인식 대기는 추출 잡이 아니라 문서 상태로 센다 — 잡은 재시도·스윕 중에도
  -- 문서가 기다리는 상태는 하나다 (ADR-052 결정 4).
  SELECT count(*) FILTER (WHERE extraction_status = 'pending') AS extraction_pending,
         count(*) FILTER (WHERE extraction_status = 'failed') AS extraction_failed
  FROM documents
), previews AS (
  SELECT count(*) FILTER (WHERE status = 'pending') AS preview_pending,
         count(*) FILTER (WHERE status = 'failed') AS preview_failed,
         count(*) FILTER (WHERE status = 'unavailable') AS preview_unavailable
  FROM document_file_previews
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
       e.stale_edge_documents,
       x.extraction_pending, x.extraction_failed,
       p.preview_pending, p.preview_failed, p.preview_unavailable
FROM job_counts j CROSS JOIN consistency s CROSS JOIN stale_edges e CROSS JOIN extraction x CROSS JOIN previews p
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
    job_lease_seconds: int
    last_job_finished_at: datetime | None
    inconsistent_documents: int
    stale_edge_documents: int
    extraction_pending: int
    extraction_failed: int
    preview_pending: int
    preview_failed: int
    preview_unavailable: int
    embedding_provider: str


async def get_system_status(
    conn: psycopg.AsyncConnection,
    *,
    job_lease_seconds: int,
    embedding_provider: str,
) -> SystemStatusResult:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(SYSTEM_STATUS_SQL)
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
        job_lease_seconds=job_lease_seconds,
        last_job_finished_at=row["last_job_finished_at"],
        inconsistent_documents=row["inconsistent_documents"],
        stale_edge_documents=row["stale_edge_documents"],
        extraction_pending=row["extraction_pending"],
        extraction_failed=row["extraction_failed"],
        preview_pending=row["preview_pending"],
        preview_failed=row["preview_failed"],
        preview_unavailable=row["preview_unavailable"],
        embedding_provider=embedding_provider,
    )


@dataclass(frozen=True)
class EdgeRebuild:
    """전량 재계산 요청 하나. `last_job_id`까지의 관계 잡이 이 요청이 기다릴 잡이다."""

    documents: int
    last_job_id: int


async def enqueue_edge_rebuild(conn: psycopg.AsyncConnection) -> EdgeRebuild:
    """ready 문서 전부에 관계 잡을 건다. 판정은 하지 않는다 — 워커 하나가 한다 (#156).

    잡을 만드는 것은 DB 함수다 — 앱은 embedding_jobs에 INSERT하지 않는다. 같은 트랜잭션에서
    잡 id 상한을 읽어 둔다: 그 아래의 관계 잡(이 요청이 건 잡과, 코얼레싱되어 대신 일하는
    기존 대기 잡)을 기다리고, 요청 뒤에 생긴 잡은 기다리지 않는다.
    """
    async with conn.transaction():
        (documents,) = await (await conn.execute("SELECT enqueue_all_edge_jobs()")).fetchone()
        # 따로 읽는다 — 한 문장의 서브쿼리는 함수가 잡을 넣기 전의 스냅샷을 본다.
        (last_job_id,) = await (
            await conn.execute("SELECT coalesce(max(id), 0) FROM embedding_jobs")
        ).fetchone()
    return EdgeRebuild(documents=documents, last_job_id=last_job_id)


async def enqueue_preview_rebuild(conn: psycopg.AsyncConnection) -> int:
    """변환본이 없거나 실패·변환기 없음인 판에 변환 잡을 건다. 변환은 하지 않는다 — 워커가 한다.

    판 행과 잡은 DB 함수가 만든다(ADR-058 결정 2) — 앱은 embedding_jobs·document_file_previews에
    쓰지 않는다. 돌려주는 값은 변환 대기(pending) 판 수다.
    """
    async with conn.transaction():
        (plates,) = await (await conn.execute("SELECT enqueue_all_preview_jobs()")).fetchone()
    return plates


async def _edge_rebuild_state(conn: psycopg.AsyncConnection, request: EdgeRebuild) -> tuple[int, int]:
    """(아직 처리되지 않은 요청 잡 수, 관계 판정이 격리된 문서 수)."""
    # 트랜잭션 안에서 읽는다 — 밖의 SELECT는 HA에서 replica로 가 진행을 늦게 본다 (ADR-010).
    # 격리는 ready 문서만 센다 — 재계산이 잡을 거는 대상이 그것뿐이라, 나머지의 격리 잡은
    # 다시 실행해도 닫히지 않는다. 그 문서는 다음 ready 전이의 관계 잡이 맞춘다 (017).
    async with conn.transaction():
        cur = await conn.execute(
            """
            SELECT count(*) FILTER (WHERE j.status IN ('pending', 'processing') AND j.id <= %s),
                   count(DISTINCT j.document_id)
                     FILTER (WHERE j.status = 'error' AND d.embedding_status = 'ready')
            FROM embedding_jobs j JOIN documents d ON d.id = j.document_id
            WHERE j.kind = 'edges'
            """,
            (request.last_job_id,),
        )
        return await cur.fetchone()


async def wait_for_edge_jobs(
    conn: psycopg.AsyncConnection,
    request: EdgeRebuild,
    *,
    poll_interval: float = 1.0,
    stall_after: float = 30.0,
    timeout: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_stall: Callable[[], None] | None = None,
) -> int:
    """워커가 요청의 관계 잡을 비울 때까지 기다리고, 판정이 격리된 문서 수를 돌려준다.

    `stall_after`초 동안 남은 잡이 줄지 않으면 `on_stall`을 한 번 부른다 — 워커가 떠 있지
    않으면 잡은 큐에 남은 채 줄지 않는다. 다시 줄기 시작하면 다음 정체에 또 부른다.
    `timeout`을 넘기면 TimeoutError다. 잡은 그대로 남아 워커가 뜨면 처리된다.

    격리된 문서 수는 이 요청에 한정하지 않는다 — 요청 전부터 격리돼 있던 ready 문서도 센다.
    ready가 아닌 문서는 세지 않는다(재계산 대상이 아니다).
    """
    loop = asyncio.get_running_loop()
    started = last_moved = loop.time()
    total = remaining = None
    stalled = False
    while True:
        current, isolated = await _edge_rebuild_state(conn, request)
        # 잡 id는 커밋 순서가 아니어서 상한 아래 잡이 늦게 커밋되면 남은 수가 늘 수 있다.
        if total is None or current > total:
            total = current
        if current != remaining:
            remaining, last_moved, stalled = current, loop.time(), False
            if on_progress is not None:
                on_progress(total - remaining, total)
        if remaining == 0:
            return isolated
        now = loop.time()
        if not stalled and now - last_moved >= stall_after:
            stalled = True
            if on_stall is not None:
                on_stall()
        if timeout is not None and now - started >= timeout:
            raise TimeoutError
        await asyncio.sleep(poll_interval)


@dataclass(frozen=True)
class ReextractSummary:
    changed: int
    unchanged: int
    failed: list[tuple[UUID, str]]
    # 최신 원본이 OCR 대상이라 텍스트를 쓰지 않고 추출 잡으로 넘긴 문서 수 (ADR-052 결정 6)
    awaiting_ocr: int = 0


# 보관된 원본에서 텍스트를 얻지 못한 실패. 문서의 문제이지 명령의 문제가 아니다.
_EXTRACTION_FAILURES = (
    ValueError,  # 파서 실패 — UnsupportedFileType·TextDecodeError 포함
    EmptyExtractedText,
    ExtractedTextTooLarge,
)

# 문서 하나의 문제로 끝나는 실패. 그 밖의 예외(연결 끊김 등)는 전체를 멈춘다 —
# 삼키면 나머지 문서가 전부 같은 이유로 실패한 채 "실패 N건"으로 요약된다.
_PER_DOCUMENT_FAILURES = (
    *_EXTRACTION_FAILURES,
    VersionConflict,
    DocumentNotFound,  # 대상 조회 뒤 삭제됐다
    ExtractionInProgress,  # 인식이 끝나지 않았다 — 그 결과를 덮지 않는다
)


async def reextract_one(
    conn: psycopg.AsyncConnection, document_id: UUID
) -> ReextractSummary:
    """문서 한 건을 보관된 최신 원본에서 다시 추출한다. 운영자 경로라 권한을 묻지 않는다.

    기대 버전은 지금 버전이다. 조회와 재추출을 한 트랜잭션에 두어 그 사이 편집을 막는다.
    한 건을 지정한 명령이므로 문서가 없거나 원본이 없으면 요약의 실패가 아니라 예외다.
    """
    try:
        async with conn.transaction():
            await set_actor(conn, actor=None, via="cli")
            row = await (
                await conn.execute(
                    "SELECT version FROM documents WHERE id = %s FOR UPDATE",
                    (document_id,),
                )
            ).fetchone()
            if row is None:
                raise DocumentNotFound
            document, changed = await reextract_text(
                conn, document_id, expected_version=row[0]
            )
    except (*_EXTRACTION_FAILURES, ExtractionInProgress) as error:
        return ReextractSummary(
            changed=0, unchanged=0, failed=[(document_id, str(error) or type(error).__name__)]
        )
    if document["extraction_status"] == "pending":
        return ReextractSummary(changed=0, unchanged=0, failed=[], awaiting_ocr=1)
    return ReextractSummary(changed=int(changed), unchanged=int(not changed), failed=[])


async def reextract_all(
    conn: psycopg.AsyncConnection,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> ReextractSummary:
    """원본이 있는 문서 전부를 보관된 최신 원본에서 다시 추출한다. 파서를 고친 뒤 쓴다.

    대상 조회와 문서별 처리를 각각 커밋해, 중간 실패가 이미 처리한 문서를 되돌리거나 잠금을
    오래 잡지 않게 한다. 문서마다 조회 시점의 버전을 기대 버전으로 넘기므로, 그 사이 누가
    편집한 문서는 덮지 않고 실패로 알린다. 원본 없는 문서는 대상이 아니다 — 실패가 아니라 정상 상태다.
    """
    async with conn.transaction():
        cur = await conn.execute(
            """
            SELECT d.id, d.version FROM documents d
            WHERE EXISTS (SELECT 1 FROM document_files f WHERE f.document_id = d.id)
            ORDER BY d.created_at, d.id
            """
        )
        documents = await cur.fetchall()
    total = len(documents)
    changed = unchanged = awaiting_ocr = 0
    failed: list[tuple[UUID, str]] = []
    for done, (document_id, version) in enumerate(documents, start=1):
        try:
            async with conn.transaction():
                await set_actor(conn, actor=None, via="cli")
                document, was_changed = await reextract_text(
                    conn, document_id, expected_version=version
                )
        except _PER_DOCUMENT_FAILURES as error:
            failed.append((document_id, str(error) or type(error).__name__))
        else:
            if document["extraction_status"] == "pending":
                awaiting_ocr += 1
            elif was_changed:
                changed += 1
            else:
                unchanged += 1
        if on_progress is not None:
            on_progress(done, total)
    return ReextractSummary(
        changed=changed, unchanged=unchanged, failed=failed, awaiting_ocr=awaiting_ocr
    )
