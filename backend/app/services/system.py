from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.services.documents import (
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
       x.extraction_pending, x.extraction_failed
FROM job_counts j CROSS JOIN consistency s CROSS JOIN stale_edges e CROSS JOIN extraction x
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

    재계산한 문서의 **격리된 관계 잡은 함께 마감한다.** error로 격리된 잡은 워커가 다시
    집지 않으므로, 이 명령이 그 잡이 요구한 일을 한 것이다. 마감하지 않으면 관계를 실제로
    복구하고도 관계 미반영 카운터가 영영 내려오지 않는다 — OPERATIONS가 이 명령을 그
    상태의 복구 경로로 안내한다.
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
            # pending·processing은 건드리지 않는다 — 그것은 워커가 가진 잡이고, 여기서
            # 청크를 읽은 뒤에 커밋된 ready 전이의 잡일 수도 있다. 함께 마감하면 마지막
            # 청크 교체가 반영되지 않은 관계를 가진 채 카운터만 0이 된다.
            await conn.execute(
                """
                UPDATE embedding_jobs SET status = 'done', finished_at = clock_timestamp()
                 WHERE document_id = %s AND kind = 'edges' AND status = 'error'
                """,
                (document_id,),
            )
        if on_progress is not None:
            on_progress(done, total)
    return total


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

    `rebuild_all_edges`와 같은 이유로 대상 조회와 문서별 처리를 각각 커밋한다. 문서마다
    조회 시점의 버전을 기대 버전으로 넘기므로, 그 사이 누가 편집한 문서는 덮지 않고
    실패로 알린다. 원본 없는 문서는 대상이 아니다 — 실패가 아니라 정상 상태다.
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
