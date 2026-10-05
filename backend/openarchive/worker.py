"""임베딩 워커 — DB가 만들어 둔 잡을 집어가는 무상태 실행기 (ARCHITECTURE "워커 처리 루프").

잡은 세 종류다 (`embedding_jobs.kind`). `embed`는 청킹·임베딩·청크 교체이고, `edges`는
이미 저장된 청크 벡터로 관계를 다시 판정하며, `extract`는 최신 원본 판을 OCR해 문서 텍스트를
채운다 — 같은 큐를 쓰되 **각자의 트랜잭션**에서 돌아, 관계 판정이 실패해도 청크와 `ready`가
남는다 (ADR-029 결정 3 개정). 추출 결과를 쓰면 기존 트리거가 텍스트 버전과 임베딩 잡을
이어서 만든다 (ADR-052 결정 3).

잡 생성·코얼레싱·삭제 정합성은 전부 DB 계층(트리거·파셜 유니크 인덱스·CASCADE)이
보장하므로, 워커의 책임은 둘뿐이다.

1. 잡을 안전하게 집어가기 — `FOR UPDATE SKIP LOCKED`
2. 커밋 직전 "내가 읽은 내용이 아직 최신인가" 확인 — `content_hash` 재확인

2번이 멀티 워커 정합성의 핵심이다. 없으면 두 워커가 경쟁할 때 낡은 버전이 최종
상태로 남아 "최신 수렴"(ADR-015)이 무너진다.

기동은 폴링이 주 경로다. LISTEN/NOTIFY는 지연을 줄이는 최적화일 뿐이며, OpenProxy
경유 동작이 문서로 보장되지 않으므로 실패해도 워커는 폴링으로 계속 돈다 (ADR-009).
마이그레이션은 실행하지 않는다 — 상시 프로세스 중 실행 주체는 API 서버 하나다 (ADR-012).

**프로세스의 생사는 이 모듈의 책임이 아니다** (ADR-038). SIGKILL·OOM으로 죽으면
스스로 살아날 수 없고, 배포 호스트에서는 systemd 유닛이 되살린다. 여기서 다루는 것은
그 죽음이 남긴 상태뿐이다: 정상 종료(SIGTERM)는 선점을 반납하고, 비정상 종료나 끊긴
연결이 남긴 좀비는 lease가 만료된 뒤 `sweep_zombies`가 재시도 예산과 함께 정리한다
(ADR-050).

잡 처리 함수들은 커넥션을 인자로 받는다 — 테스트가 커넥션 두 개로 워커 경쟁을
재현하기 위함이다. 커넥션은 **autocommit이어야 한다**: 아니면 load의 SELECT가 연
암묵 트랜잭션 탓에 이후 `transaction()` 블록이 SAVEPOINT로 바뀌어, claim의
"즉시 커밋"이 조용히 사라진다.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

import psycopg

from openarchive.config import get_settings
from openarchive.db import close_pool, connection, get_pool, keepalive_kwargs
from openarchive.embeddings import EmbeddingProvider, get_provider, warm_up
from openarchive.services.audit import set_actor
from openarchive.services.chunking import chunk_text
from openarchive.services.documents import apply_extracted_text
from openarchive.services.parsing import detect_content_type, ocr_text
from openarchive.vectors import to_pgvector_literal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
MAX_ATTEMPTS = 3

# 좀비 회수로 예산을 소진한 잡의 last_error. fail_job이 남기는 `타입: 메시지` 형식을
# 따라, UI가 두 경로의 실패를 같은 모양으로 보여줄 수 있게 한다.
ZOMBIE_EXHAUSTED_ERROR = (
    "WorkerCrashLoop: 워커가 반복적으로 비정상 종료해 재시도 예산을 소진했다"
)

CHANNEL = "embedding_jobs"

# heartbeat가 lease를 빌려 쓸 연결 공급자. run_worker에서는 풀(`openarchive.db.connection`)이다.
LeaseConnection = Callable[[], AbstractAsyncContextManager[psycopg.AsyncConnection]]

# embedding_jobs.kind (016). 큐·claim·재시도·좀비 회수는 공유하고 처리 본체만 갈린다.
EMBED_JOB_KIND = "embed"
EDGE_JOB_KIND = "edges"
EXTRACT_JOB_KIND = "extract"


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    document_id: UUID
    kind: str
    # 선점마다 오르고 스윕은 건드리지 않는다 — (job_id, attempts)가 "이 선점"을 가리킨다.
    # 회수 뒤 다른 워커가 다시 집은 잡은 id가 같아도 attempts가 다르다 (ADR-050 결정 2).
    attempts: int


def heartbeat_interval() -> float:
    """heartbeat 한 주기(초) — lease의 1/3. 두 번 놓쳐도 DB 쪽 lease가 살아 있는 간격이다."""
    return get_settings().job_lease_seconds / 3


async def bound_lock_wait(conn: psycopg.AsyncConnection) -> None:
    """이 트랜잭션의 락 대기를 heartbeat 한 주기(lease의 1/3)로 묶는다 (#128). 트랜잭션 첫 문장으로 부른다.

    자기 잡에 쓰는 트랜잭션(lease 연장·반영·실패 기록·반납)은 남이 그 문서·잡을 잠깐 쥐고
    있으면 기다려야 한다. 그러나 쥔 쪽이 죽은 OpenProxy 노드 너머의 고아 트랜잭션이면 Primary가
    그것을 끊을 때까지(서버 keepalive 기본 2시간) 풀리지 않는다 — #122 S5a-2에서 lease 연장이
    761초 넘게 멈췄다. 상한에 걸리면 `LockNotAvailable`로 끝나고 잡은 lease 만료 뒤 스윕이 회수한다.

    한 주기인 이유: 연장 한 번의 대기가 다음 주기를 삼키지 않아야 lease(세 주기) 안에 다시
    시도할 수 있다. `SET LOCAL`과 같지만 값을 인자로 넘기려고 `set_config(..., true)`를 쓴다 —
    OpenProxy transaction 모드라 트랜잭션 밖 SET은 쓸 수 없다.
    """
    milliseconds = int(heartbeat_interval() * 1000)
    await conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{milliseconds}ms",))


async def claim_job(conn: psycopg.AsyncConnection) -> ClaimedJob | None:
    """pending 잡 하나를 processing으로 선점하고 **즉시 커밋**한다. 없으면 None.

    임베딩은 오래 걸린다 — 트랜잭션을 열어둔 채 처리하면 잡 행 잠금이 유지되어 다른
    워커의 claim이 막히고, processing 배지가 UI에 보이지도 않는다. 그래서 선점만
    커밋하고, 결과 반영은 finalize_job의 별도 트랜잭션이 맡는다.

    **종류를 가리지 않고 id 순으로 집는다.** 관계 잡에 우선순위를 주면 그 판정이 뒤에
    오는 문서의 임베딩보다 먼저 돌아, 아직 청크가 없는 이웃을 못 보고 관계를 놓친다.

    선점은 lease와 함께다 (ADR-050). 처리하는 동안 heartbeat가 연장하지 않으면 lease 뒤에
    스윕이 회수한다.

    **문서 행이 잠긴 잡은 건너뛴다** (#128). 임베딩 잡은 아래에서 문서 행을 UPDATE하는데, 그 행을
    고아 트랜잭션이 쥐고 있으면 기다리는 동안 워커가 서고, 상한을 걸어 실패시켜도 다음 주기에
    id가 가장 작은 같은 잡을 또 집어 뒤의 잡이 영영 오지 않는다. 문서 행을 잡 행과 함께
    `SKIP LOCKED`로 잠가 두면 아래 UPDATE도 기다릴 일이 없다. 수정 중인 문서의 잡을 잠깐 건너뛰는
    것뿐이다 — 다음 선점이 가져간다. 관계 잡은 선점에서 문서 행을 고치지 않지만 종류를 가리지
    않고 함께 건너뛴다 — 판정 트랜잭션이 어차피 그 문서 행을 먼저 잠그므로 집어 봐야 기다린다.
    """
    async with conn.transaction():
        cur = await conn.execute(
            """
            UPDATE embedding_jobs j
               SET status = 'processing', attempts = attempts + 1, started_at = now(),
                   lease_expires_at = now() + make_interval(secs => %s)
             WHERE j.id = (SELECT q.id FROM embedding_jobs q
                             JOIN documents d ON d.id = q.document_id
                            WHERE q.status = 'pending' AND q.next_attempt_at <= now()
                            ORDER BY q.id LIMIT 1
                              FOR UPDATE OF q SKIP LOCKED
                              FOR NO KEY UPDATE OF d SKIP LOCKED)
            RETURNING j.id, j.document_id, j.kind, j.attempts
            """,
            (get_settings().job_lease_seconds,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        job = ClaimedJob(job_id=row[0], document_id=row[1], kind=row[2], attempts=row[3])

        if job.kind == EMBED_JOB_KIND:
            # 관계 잡에는 걸지 않는다 — 청크는 그대로인데 배지가 processing으로 돌아가면
            # 사용자에게는 재임베딩으로 보인다. 관계 미반영은 별도 카운터가 관측한다.
            # SET 절에 content_hash가 없으므로 트리거는 발화하지 않는다 (UI 표시용 전환).
            await conn.execute(
                """
                UPDATE documents SET embedding_status = 'processing'
                 WHERE id = %s AND embedding_status <> 'processing'
                """,
                (job.document_id,),
            )
    return job


async def extend_lease(conn: psycopg.AsyncConnection, job: ClaimedJob) -> bool:
    """잡의 lease를 지금부터 다시 한 lease만큼 연장한다. 연장했으면 True.

    자기 선점이 여전히 processing일 때만 성공한다 (ADR-050 결정 2). lease가 지나 스윕이
    회수한 잡을 되살리거나, 다른 워커가 다시 집은 잡의 lease를 대신 늘리면 두 워커가 한
    잡을 제 것으로 여긴다. 0행이면 잡을 잃은 것이다.
    """
    async with conn.transaction():
        await bound_lock_wait(conn)
        cur = await conn.execute(
            """
            UPDATE embedding_jobs
               SET lease_expires_at = now() + make_interval(secs => %s)
             WHERE id = %s AND status = 'processing' AND attempts = %s
            """,
            (get_settings().job_lease_seconds, job.job_id, job.attempts),
        )
    return cur.rowcount == 1


async def _keep_lease(
    job: ClaimedJob, lease_conn: LeaseConnection, lost: asyncio.Event, finished: asyncio.Event
) -> None:
    """`finished`가 설 때까지 lease의 1/3마다 연장한다. 잡을 잃으면 `lost`를 세우고 끝난다.

    처리가 끝나도 취소하지 않고 `finished`로 멈춘다 — 연장 도중에 취소하면 풀 연결이
    트랜잭션 중간에 반납되고, `CancelledError`는 DB 오류가 아니라 `openarchive.db.connection`의
    폐기 분기도 타지 않는다(#110 B-2). 대기 중이던 연장 한 번만큼 처리 마감이 늦어진다.

    처리 연결과 **다른 연결**로 한다 — 처리 연결은 임베딩 동안 비어 있지만 finalize의
    트랜잭션과 겹칠 수 있고, 연장이 처리 흐름에 끼어들면 잠금 순서가 섞인다.

    연결 오류 한 번으로는 포기하지 않는다. lease가 주기의 세 배라 두 번까지는 놓쳐도
    DB 쪽 lease가 살아 있다. 마지막 성공 뒤 lease가 통째로 지나면 그때는 스윕이 이미
    회수했을 수 있으므로 잃은 것으로 다룬다.
    """
    lease = get_settings().job_lease_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + lease
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(finished.wait(), timeout=heartbeat_interval())
        if finished.is_set():
            return
        try:
            async with lease_conn() as hb:
                extended = await extend_lease(hb, job)
        except Exception:
            if loop.time() >= deadline:
                logger.warning(
                    "lease를 %s초 동안 연장하지 못했다 — 잡을 잃은 것으로 본다 (job_id=%s)",
                    lease,
                    job.job_id,
                    exc_info=True,
                )
                lost.set()
                return
            logger.warning(
                "lease 연장 실패 — 다음 주기에 다시 시도한다 (job_id=%s)", job.job_id, exc_info=True
            )
            continue
        if not extended:
            logger.warning("잡이 더 이상 이 워커의 것이 아니다 — 회수됐다 (job_id=%s)", job.job_id)
            lost.set()
            return
        deadline = loop.time() + lease


async def load_document(conn: psycopg.AsyncConnection, document_id: UUID) -> tuple[str, str] | None:
    """(content, content_hash)를 읽는다. 문서가 이미 삭제됐으면 None.

    version은 여기서 읽지 않는다 — 본문이 A → B → A로 되돌아오면 content_hash는
    원래대로지만 version은 2 올라 있어, 해시 재확인은 통과하는데 여기서 읽은 version은
    낡은 값이 된다. version은 finalize_job의 FOR UPDATE 아래에서 읽는다.

    읽기 하나지만 **반드시 트랜잭션 안에서** 한다. HA의 OpenProxy는 트랜잭션 밖 SELECT를
    replica로 보내고, 비동기 복제라 replica는 방금 커밋된 문서를 아직 모를 수 있다 —
    그러면 삭제로 오인해 잡을 마감하거나 옛 본문을 임베딩해 폐기하고, 새 잡이 없으니
    문서가 processing에 멈춘다 (OPENSQL_RESEARCH §6, #110).
    """
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT content, content_hash FROM documents WHERE id = %s", (document_id,)
        )
        row = await cur.fetchone()
    return (row[0], row[1]) if row is not None else None


async def mark_job_done(conn: psycopg.AsyncConnection, job_id: int) -> None:
    """잡을 done으로 마감한다 — 일을 마친 경우도, 낡아서 폐기한 경우도 같은 마감이다.

    `now()`가 아니라 `clock_timestamp()`를 쓴다. now()는 트랜잭션 시작 시각이라 같은
    트랜잭션 안에서 한 일(청크 교체·관계 판정·AFTER 트리거의 잡 기록)의 시간이 통째로
    빠진다.
    """
    await conn.execute(
        "UPDATE embedding_jobs SET status = 'done', finished_at = clock_timestamp() WHERE id = %s",
        (job_id,),
    )


async def lock_owned_job(conn: psycopg.AsyncConnection, job: ClaimedJob) -> bool:
    """잡이 아직 이 선점의 것이면 잠그고 True — 결과·실패를 쓰기 전에 트랜잭션 안에서 부른다.

    heartbeat의 `lost`는 늦게 설 수 있다 — 연장 주기 사이, 풀 대여를 기다리는 동안, 확인과
    반영 사이. 그 사이 스윕이 되돌렸거나 다른 워커가 다시 집은 잡에 쓰면 남의 선점을
    지우므로, 판정은 쓰는 순간 DB가 한다 (ADR-050 결정 2). 문서 행을 먼저 잠근 뒤에
    부른다 — sweep_zombies와 같은 순서라, 이 잠금이 풀릴 때까지 스윕이 잡을 바꾸지 못한다.
    """
    cur = await conn.execute(
        "SELECT 1 FROM embedding_jobs"
        " WHERE id = %s AND status = 'processing' AND attempts = %s FOR UPDATE",
        (job.job_id, job.attempts),
    )
    return await cur.fetchone() is not None


async def finalize_job(
    conn: psycopg.AsyncConnection,
    job: ClaimedJob,
    expected_hash: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> bool:
    """임베딩 결과를 단일 트랜잭션으로 반영한다. 반영했으면 True, 폐기했으면 False.

    잡을 잃었으면(`lock_owned_job`) 아무것도 쓰지 않고 False다.
    """
    async with conn.transaction():
        await bound_lock_wait(conn)
        # 커밋 직전 재확인 — 멀티 워커 정합성의 핵심이다. 잠금 없이 비교하면 비교와
        # 커밋 사이에 문서가 또 바뀔 수 있고, 낡은 결과가 최신 결과를 덮어쓴다.
        cur = await conn.execute(
            "SELECT content_hash, version FROM documents WHERE id = %s FOR UPDATE",
            (job.document_id,),
        )
        row = await cur.fetchone()
        if row is None:
            # 문서가 삭제됐다 — 잡·청크도 CASCADE로 이미 사라졌다. 쓸 곳이 없다.
            return False
        if not await lock_owned_job(conn, job):
            logger.warning("잃은 잡의 결과를 버린다 — job_id=%s", job.job_id)
            return False
        current_hash, version = row

        if current_hash != expected_hash:
            # 처리 도중 문서가 수정됐다 — 이 결과는 낡았으므로 폐기한다. 트리거가 만든
            # 새 pending 잡이 최신 내용으로 다시 처리하므로 실패가 아니라 마감이다.
            # 실패 처리하면 재시도 횟수만 소모한다.
            await mark_job_done(conn, job.job_id)
            return False

        # 교체는 DELETE+INSERT가 같은 트랜잭션이어야 한다 — 다른 세션이 중간 상태를
        # 보면 "활성 청크는 항상 하나의 버전"(ADR-015)이 깨진다.
        await conn.execute(
            "DELETE FROM document_chunks WHERE document_id = %s", (job.document_id,)
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            # version은 반드시 위 FOR UPDATE로 읽은 값이다 — 정합성 검증 쿼리
            # (c.version <> d.version)와 /admin/status 카운터의 근거 컬럼이라,
            # 잘못 채우면 지표 자체가 무의미해진다.
            await conn.execute(
                """
                INSERT INTO document_chunks (document_id, version, chunk_index, content, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                """,
                (job.document_id, version, index, chunk, to_pgvector_literal(vector)),
            )
        await conn.execute(
            "UPDATE documents SET embedding_status = 'ready' WHERE id = %s",
            (job.document_id,),
        )
        await mark_job_done(conn, job.job_id)
    return True


async def finalize_edge_job(conn: psycopg.AsyncConnection, job: ClaimedJob) -> bool:
    """관계를 **자기 트랜잭션**에서 다시 판정한다. 판정했으면 True, 문서가 없거나 잡을
    잃었으면 False.

    판정 본체는 DB 함수 `rebuild_document_edges` 하나다 (014). 워커는 규칙을 복제하지
    않는다 — 판정이 DB 안에 있다는 것이 이 과제의 주장이기 때문이다. 관계를 쓰는 곳도 이
    잡 하나다: `openarchive rebuild-edges`의 전량 재계산은 판정을 직접 부르지 않고 모든
    ready 문서에 관계 잡을 건다 (ADR-029 결정 6 개정, #156).

    임베딩 잡과 달리 **낡았다는 이유로 폐기하지 않는다.** 재임베딩이 시작된 문서라도
    지금 있는 청크로 판정한다. 폐기의 전제인 "새 ready 전이가 새 잡을 만든다"는
    재임베딩이 성공할 때만 참이고, 재시도 예산을 소진해 error로 끝나면 ready 전이가
    영영 오지 않아 관계를 한 번도 계산하지 않은 문서가 남는다 — 그런데 잡은 done이라
    관계 미반영 카운터는 0을 보고한다. 곧 교체될 청크로 계산하는 손해는 판정 한 번이고,
    재임베딩이 끝나면 그 ready 전이의 새 잡이 다시 계산한다 (ADR-029 결정 3 개정).

    documents는 한 컬럼도 UPDATE하지 않는다. `embedding_status`는 임베딩 잡이 쓰는
    칸이므로 — 이 잡이 도는 시점의 값이 무엇이든 — 관계 판정의 성패가 그것을 건드려서는
    안 된다. 청크가 멀쩡해 검색이 되는데 "임베딩 실패" 배지가 뜨면 화면이 거짓말을 한다.
    """
    async with conn.transaction():
        await bound_lock_wait(conn)
        # fail_job·sweep_zombies와 같은 잠금 순서다. 판정과 결과 기록 사이에 청크가
        # 교체되면 방금 계산한 관계가 사라진 청크 번호를 가리킨다. `FOR NO KEY UPDATE`인
        # 이유는 함수와 같다(024) — 청크 교체는 막고, 이 문서를 가리키는 관계 INSERT의
        # FK 검사는 막지 않는다. `FOR UPDATE`면 서로의 이웃을 동시에 판정할 때 교착한다.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR NO KEY UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            # 문서가 삭제됐다 — 잡·관계도 CASCADE로 이미 사라졌다. 쓸 곳이 없다.
            return False
        if not await lock_owned_job(conn, job):
            logger.warning("잃은 관계 잡을 판정하지 않는다 — job_id=%s", job.job_id)
            return False

        await conn.execute("SELECT rebuild_document_edges(%s)", (job.document_id,))
        await mark_job_done(conn, job.job_id)
        # 판정은 문서의 관계를 통째로 교체하므로 앞서 격리된 관계 잡이 요구한 일도 했다.
        # 마감하지 않으면 관계를 복구하고도 관계 미반영 카운터가 내려오지 않는다 (#156).
        # last_error는 남긴다 — 왜 격리됐었는지는 기록이다.
        await conn.execute(
            """
            UPDATE embedding_jobs SET status = 'done', finished_at = clock_timestamp()
             WHERE document_id = %s AND kind = 'edges' AND status = 'error'
            """,
            (job.document_id,),
        )
    return True


async def load_original_file(
    conn: psycopg.AsyncConnection, document_id: UUID
) -> tuple[str, bytes] | None:
    """최신 원본 판의 (파일명, 바이트)를 읽는다. 문서가 삭제됐으면 None.

    `load_document`와 같은 이유로 트랜잭션 안에서 읽는다 — 트랜잭션 밖 SELECT는 HA에서
    replica로 가서 방금 올린 원본을 아직 모를 수 있다.
    """
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT filename, data FROM document_files WHERE document_id = %s"
            " ORDER BY file_version DESC LIMIT 1",
            (document_id,),
        )
        row = await cur.fetchone()
    return (row[0], bytes(row[1])) if row is not None else None


async def finalize_extract_job(conn: psycopg.AsyncConnection, job: ClaimedJob, text: str) -> bool:
    """OCR 결과를 **자기 트랜잭션**에서 반영한다. 반영을 시도했으면 True, 문서가 없거나
    잡을 잃었으면 False (ADR-052 결정 3·8).

    반영 결과가 `failed`(빈 결과·크기 초과)여도 잡은 done이다. 같은 원본이면 같은 결과라
    재시도는 예산만 쓰고 늦게 같은 결론에 닿는다 — 문서의 `extraction_status='failed'`가
    그 사실을 남긴다. `skipped`(더 이상 추출 중이 아닌 문서)도 쓸 것이 없으므로 마감이다.
    """
    async with conn.transaction():
        await bound_lock_wait(conn)
        # fail_job·sweep_zombies와 같은 잠금 순서 — 문서 행 먼저, 그다음 잡.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            return False  # 문서 삭제 — 잡도 CASCADE로 이미 사라졌다
        if not await lock_owned_job(conn, job):
            logger.warning("잃은 추출 잡의 결과를 버린다 — job_id=%s", job.job_id)
            return False
        # 재추출이 만드는 새 텍스트 버전의 감사 행위자 — 요청한 사람은 워커가 모른다 (ADR-055).
        await set_actor(conn, actor=None, via="worker")
        outcome = await apply_extracted_text(conn, job.document_id, text)
        if outcome == "failed":
            logger.warning(
                "텍스트를 인식하지 못했다 — document_id=%s (재시도하지 않는다)", job.document_id
            )
        await mark_job_done(conn, job.job_id)
    return True


async def _mark_extraction_failed(conn: psycopg.AsyncConnection, document_ids: list[UUID]) -> None:
    """예산을 소진한 추출 잡의 문서를 `failed`로 둔다 — `embedding_status`는 건드리지 않는다.

    인식 실패와 임베딩 실패는 사용자가 할 일이 다르다(원본 교체 vs 재임베딩, ADR-052 결정 4).
    추출 중인 문서만 바꾼다 — 그사이 다른 경로가 완료로 만든 문서를 되돌리지 않는다.
    """
    await conn.execute(
        "UPDATE documents SET extraction_status = 'failed', updated_at = now()"
        " WHERE id = ANY(%s) AND extraction_status = 'pending'",
        (document_ids,),
    )


async def fail_job(conn: psycopg.AsyncConnection, job: ClaimedJob, error: Exception) -> None:
    """실패한 잡을 지수 백오프로 재시도 대기시키거나, 소진되면 error로 마감한다.

    documents.embedding_status는 pending으로 되돌리지 않는다 — 재시도 대기 중에도
    사용자에게는 처리 중이 맞고, 상태를 pending으로 돌리는 것은 트리거의 책임이다.
    """
    message = f"{type(error).__name__}: {error}"
    async with conn.transaction():
        await bound_lock_wait(conn)
        # 문서 행을 먼저 잠근다. 잡 생성은 전부 documents 변경 트리거 안에서 일어나므로,
        # 이 잠금이 아래 "다른 pending 잡이 있는가" 판정과 pending 복귀 사이에 새 잡이
        # 끼어드는 것(uq_pending_job_per_doc 위반)을 막는다.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            return  # 문서 삭제 — 잡도 CASCADE로 소멸했으니 남길 것이 없다
        if not await lock_owned_job(conn, job):
            return  # 잡을 잃었다 — 남의 선점에 실패를 기록하지 않는다

        # 같은 종류 안에서만 본다 — uq_pending_job_per_doc_kind(016)가 (문서, 종류)당
        # pending 1개를 강제하므로, 다른 종류의 대기 잡은 이 잡의 복귀를 막지 않는다.
        cur = await conn.execute(
            "SELECT 1 FROM embedding_jobs"
            " WHERE document_id = %s AND kind = %s AND status = 'pending'",
            (job.document_id, job.kind),
        )
        if await cur.fetchone() is not None:
            # 처리 중 문서가 수정되어 새 pending 잡이 생겼다. 이 잡을 pending으로 되돌리면
            # 문서당 pending 1개 제약에 걸리고, 어차피 새 잡이 최신 내용으로 처리한다.
            # finalize의 낡은 결과 폐기와 같은 원칙으로 마감한다.
            #
            # 재시도 소진 검사보다 **먼저** 본다. 이 잡은 낡은 내용을 보고 있었으므로
            # 수명이 끝난 것이지 문서가 실패한 것이 아니다. 순서를 뒤집으면 소진 시점에
            # 문서가 error로 떨어져, 새 잡이 ready로 되돌릴 때까지 거짓 배지가 뜬다.
            await conn.execute(
                """
                UPDATE embedding_jobs
                   SET status = 'done', last_error = %s, finished_at = clock_timestamp()
                 WHERE id = %s
                """,
                (message, job.job_id),
            )
            return

        cur = await conn.execute(
            "SELECT attempts FROM embedding_jobs WHERE id = %s", (job.job_id,)
        )
        (attempts,) = await cur.fetchone()  # 문서가 있으면 잡도 있다 (삭제 경로는 CASCADE뿐)

        if attempts >= MAX_ATTEMPTS:
            await conn.execute(
                """
                UPDATE embedding_jobs
                   SET status = 'error', last_error = %s, finished_at = clock_timestamp()
                 WHERE id = %s
                """,
                (message, job.job_id),
            )
            if job.kind == EMBED_JOB_KIND:
                # 관계 잡의 소진은 문서를 error로 떨어뜨리지 않는다 — 청크가 멀쩡해
                # 검색이 되는데 "임베딩 실패" 배지가 뜨면 상태 표시가 거짓말이 된다.
                await conn.execute(
                    "UPDATE documents SET embedding_status = 'error' WHERE id = %s",
                    (job.document_id,),
                )
            elif job.kind == EXTRACT_JOB_KIND:
                await _mark_extraction_failed(conn, [job.document_id])
            return

        # attempts는 claim 시점에 이미 올라 있다: 1번째 실패 → 2초, 2번째 → 4초.
        await conn.execute(
            """
            UPDATE embedding_jobs
               SET status = 'pending', last_error = %s,
                   next_attempt_at = now() + make_interval(secs => %s)
             WHERE id = %s
            """,
            (message, float(2**attempts), job.job_id),
        )


async def release_job(conn: psycopg.AsyncConnection, job: ClaimedJob) -> None:
    """정상 종료(SIGTERM) 시 선점을 반납한다 — pending 복귀 + attempts 원복.

    배포로 워커를 세우는 것은 잡의 실패가 아니다. attempts를 원복하지 않으면 배포를
    MAX_ATTEMPTS번 반복하는 것만으로 멀쩡한 문서가 sweep_zombies의 소진 판정에 걸려
    error로 격리된다. 백오프도 걸지 않는다 — 다음 워커가 곧바로 이어받아야 한다.

    반납하지 않고 죽어도 정합성은 깨지지 않는다. 다만 잡이 좀비로 남아 lease 만료(기본
    60초)까지 회수되지 않으므로, 배포마다 그만큼 파이프라인이 늦어진다.
    """
    async with conn.transaction():
        await bound_lock_wait(conn)
        # fail_job·sweep_zombies와 같은 잠금 순서다 — 판정과 기록 사이에 새 pending
        # 잡이 커밋되면 pending 복귀가 uq_pending_job_per_doc 위반으로 터진다.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            return  # 문서 삭제 — 잡도 CASCADE로 소멸했으니 반납할 곳이 없다

        cur = await conn.execute(
            "SELECT 1 FROM embedding_jobs"
            " WHERE document_id = %s AND kind = %s AND status = 'pending'",
            (job.document_id, job.kind),
        )
        if await cur.fetchone() is not None:
            # 처리 중 문서가 수정됐다 — 새 잡이 최신 내용으로 처리하므로 마감한다.
            await mark_job_done(conn, job.job_id)
            return

        # documents.embedding_status는 되돌리지 않는다 — fail_job과 같은 이유다.
        # 곧 다른 워커가 집어가므로 사용자에게는 처리 중이 맞다.
        await conn.execute(
            "UPDATE embedding_jobs SET status = 'pending', attempts = attempts - 1"
            " WHERE id = %s",
            (job.job_id,),
        )


async def sweep_zombies(conn: psycopg.AsyncConnection) -> int:
    """lease가 만료된 processing 잡을 회수한다. pending으로 복귀시킨 건수 반환.

    lease가 지났다는 것은 소유자가 heartbeat를 멈췄다는 뜻이다 — 워커가 죽었거나, 워커는
    살아 있는데 연결이 끊겼다 (ADR-050). 어느 쪽이든 잡은 버려졌다.

    반환값은 **회수한 건수만** 센다 — 예산을 소진해 error로 격리한 잡과, 문서가 이미
    수정되어 done으로 마감한 잡은 포함하지 않는다.

    attempts는 초기화하지 않는다 — 매번 초기화하면 계속 죽는 잡이 영원히 재시도된다.
    다만 유지하는 것만으로는 부족하다: `claim_job`은 attempts를 보지 않으므로, 예산을
    실제로 강제하는 것은 아래 error 마감 하나뿐이다. 그것이 없으면 재시도 상한이
    `fail_job`(예외로 잡히는 실패)에만 걸리고, 워커 프로세스를 죽이는 잡은 회수 →
    재선점 → 재크래시를 무한히 반복한다.
    """
    async with conn.transaction():
        # 판정 전에 대상 문서 행을 잠근다 — fail_job과 같은 이유다 (ARCHITECTURE 4·5번
        # 공통 예외). 잡 생성은 전부 documents 변경 트리거 안에서 일어나므로, 이 잠금이
        # 아래 두 UPDATE의 (NOT) EXISTS 판정과 상태 기록 사이에 새 pending 잡이 커밋되는
        # 것을 막는다. 잠그지 않으면 READ COMMITTED의 statement 스냅샷 탓에 그 잡을 놓쳐
        # 좀비를 pending으로 되돌리고, uq_pending_job_per_doc 위반으로 스윕이 통째로 터진다.
        # 잡보다 문서를 먼저 잠그는 순서가 문서 수정 트랜잭션과의 교착도 함께 없앤다.
        #
        # 문서도 잡도 **잠긴 것은 건너뛴다** (#128). 고아 트랜잭션이 좀비 하나의 문서나 잡을 쥐고
        # 있으면 기다리는 동안 스윕이 서고, 스윕은 루프 머리에 있어 그 주기의 drain도 돌지 않는다.
        # 건너뛴 좀비는 막은 쪽이 풀린 뒤의 스윕이 회수한다. 판정은 잠근 좀비에 대해서만 한다.
        cur = await conn.execute(
            """
            SELECT id FROM documents
             WHERE id IN (SELECT document_id FROM embedding_jobs
                           WHERE status = 'processing' AND lease_expires_at < now())
             ORDER BY id
               FOR UPDATE SKIP LOCKED
            """
        )
        documents = [row[0] for row in await cur.fetchall()]
        cur = await conn.execute(
            """
            SELECT id FROM embedding_jobs
             WHERE status = 'processing' AND lease_expires_at < now()
               AND document_id = ANY(%s)
               FOR UPDATE SKIP LOCKED
            """,
            (documents,),
        )
        zombies = [row[0] for row in await cur.fetchall()]
        if not zombies:
            return 0

        # 대상 좀비를 한 번만 뽑고 각 잡의 처분을 CTE에서 정한다. 조건을 UPDATE마다
        # 반복하면 세 문장의 실행 순서에 정합성이 의존하게 되는데, 특히 **(문서, 종류)당
        # 하나만 pending으로 되돌린다**는 제약은 문장을 나눠서는 표현할 수 없다.
        #
        #   done    — 이미 새 pending 잡이 있어 최신 내용으로 처리될 잡(superseded), 또는
        #             같은 (문서, 종류)의 좀비 중 두 번째 이후(rn > 1). 후자가 없으면 한
        #             UPDATE가 두 행을 pending으로 만들어 uq_pending_job_per_doc_kind 위반으로 스윕이
        #             통째로 터지고, run_worker의 except가 그것을 삼켜 그 주기의 drain이
        #             실행되지 않는다 — 매 폴링 반복되면 파이프라인이 영구 정지한다.
        #             잡에는 페이로드가 없어 어느 것을 남겨도 같으므로 가장 오래된 것을
        #             남긴다(작은 id). 감독자가 워커를 되살리는 경로에서만 생긴다:
        #             워커1이 죽어 좀비 → 문서 수정으로 새 잡 → 워커2가 그것을 집고 죽음.
        #   error   — 재시도 예산 소진. superseded 판정이 **먼저**라 낡은 내용을 보던 잡은
        #             실패가 아니라 수명이 끝난 것으로 다뤄진다 (fail_job과 같은 순서).
        #   pending — 그 외. 회수 대상이며 반환값이 세는 것은 이것뿐이다.
        cur = await conn.execute(
            """
            WITH zombie AS (
                SELECT j.id, j.document_id, j.kind, j.attempts,
                       row_number() OVER (
                           PARTITION BY j.document_id, j.kind ORDER BY j.id
                       ) AS rn,
                       EXISTS (SELECT 1 FROM embedding_jobs p
                                WHERE p.document_id = j.document_id
                                  AND p.kind = j.kind
                                  AND p.status = 'pending') AS superseded
                  FROM embedding_jobs j
                 WHERE j.id = ANY(%(zombies)s)
            ), decided AS (
                SELECT id, document_id, kind,
                       CASE WHEN superseded OR rn > 1     THEN 'done'
                            WHEN attempts >= %(max_attempts)s THEN 'error'
                            ELSE 'pending' END AS next_status
                  FROM zombie
            )
            UPDATE embedding_jobs j
               SET status = d.next_status,
                   last_error = CASE WHEN d.next_status = 'error'
                                     THEN %(exhausted_error)s ELSE j.last_error END,
                   finished_at = CASE WHEN d.next_status IN ('done', 'error')
                                      THEN clock_timestamp() ELSE j.finished_at END
              FROM decided d
             WHERE j.id = d.id
         RETURNING d.document_id, d.kind, d.next_status
            """,
            {
                "zombies": zombies,
                "max_attempts": MAX_ATTEMPTS,
                "exhausted_error": ZOMBIE_EXHAUSTED_ERROR,
            },
        )
        decided = await cur.fetchall()

        # 관계 잡의 소진은 제외한다 — fail_job과 같은 이유로 문서 배지를 건드리지 않는다.
        exhausted = [
            doc_id
            for doc_id, kind, status in decided
            if status == "error" and kind == EMBED_JOB_KIND
        ]
        if exhausted:
            # fail_job의 소진 처리와 같은 상태로 맞춘다. 청크는 지우지 않으므로 검색은
            # 이전 버전으로 계속되고, 정합성 카운터는 어긋난 채 남는다 — 격리했다고
            # 어긋남을 숨기면 계약이 거짓말이 된다. 재개 수단은 문서 재수정이다
            # (003_triggers.sql의 `SET content_hash = content_hash` 경로).
            await conn.execute(
                "UPDATE documents SET embedding_status = 'error' WHERE id = ANY(%s)",
                (exhausted,),
            )
        extraction_exhausted = [
            doc_id
            for doc_id, kind, status in decided
            if status == "error" and kind == EXTRACT_JOB_KIND
        ]
        if extraction_exhausted:
            await _mark_extraction_failed(conn, extraction_exhausted)

        return sum(1 for _, _, status in decided if status == "pending")


# 재시도 창(분 단위)보다 충분히 길고 테이블이 무한히 자라지 않는 값 (ADR-047 결정 3).
IDEMPOTENCY_KEY_TTL = "24 hours"


async def purge_expired_idempotency_keys(conn: psycopg.AsyncConnection) -> int:
    """보관 기한이 지난 문서 생성 멱등키를 지운다. 지운 건수를 반환한다.

    잡 처리와 무관하지만 별도 스케줄러를 두지 않으려고 스윕 주기에 얹는다(pg_cron은
    #29에서 기각). 키만 지우며 문서는 그대로다.
    """
    cur = await conn.execute(
        "DELETE FROM idempotency_keys WHERE created_at < now() - %s::interval",
        (IDEMPOTENCY_KEY_TTL,),
    )
    return cur.rowcount


async def process_once(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    stop: asyncio.Event | None = None,
    lease_conn: LeaseConnection | None = None,
) -> bool:
    """잡 하나를 처리한다. 집어간 잡이 있었으면 True, 없으면 False.

    처리 본체는 잡의 종류로 갈린다 — `embed`는 본문을 읽어 청킹·임베딩·청크 교체까지,
    `edges`는 저장된 청크 벡터로 관계만 다시 판정하고, `extract`는 최신 원본 판을 OCR해
    문서 텍스트를 채운다.

    처리 실패도 True다 — fail_job이 재시도를 예약했고, drain의 반복 조건은 "이번에
    할 일이 있었는가"이기 때문이다. 예외: 실패 기록마저 락 상한(`bound_lock_wait`)에 걸리면
    `LockNotAvailable`이 밖으로 나간다 — 반영을 막은 문서 행 락이 실패 기록도 막기 때문이다.
    run_worker가 그 연결을 버리고 다음 주기로 넘어가며, 잡은 lease 만료 뒤 스윕이 회수한다.

    `stop`은 정상 종료 신호다. **임베딩을 시작하기 전에** 확인해 반납하므로, 배포로
    세운 워커가 잡을 processing으로 붙든 채 사라지지 않는다. 이미 임베딩에 들어간
    잡은 끝까지 처리한다 — 중간에 끊어도 할 수 있는 일이 반납뿐이고, 완료가 더 나은
    결과다. 반납한 주기는 "할 일이 있었다"로 세지 않으므로 False를 돌려준다.

    `lease_conn`이 있으면 처리하는 동안 heartbeat가 lease를 연장한다 (ADR-050). 잡을
    잃으면 **임베딩 뒤·반영 전**에 포기한다 — 결과를 쓰지도, 실패로 기록하지도 않는다.
    잡은 이제 되찾아 간 쪽의 것이다. 처리 도중 태스크를 취소하지 않는 것은 트랜잭션
    중간에 끊긴 연결이 풀을 오염시키기 때문이다(#110 B-2) — heartbeat도 같은 이유로
    취소하지 않고 멈춘다. `lost`는 늦게 설 수 있으므로 이것은 헛일을 줄이는 최적화이고,
    남의 잡에 쓰지 않는 보장은 반영·실패 기록 트랜잭션의 `lock_owned_job`이 한다.
    """
    job = await claim_job(conn)
    if job is None:
        return False
    if stop is not None and stop.is_set():
        await release_job(conn, job)
        return False
    lost = asyncio.Event()
    finished = asyncio.Event()
    heartbeat = (
        asyncio.create_task(_keep_lease(job, lease_conn, lost, finished))
        if lease_conn is not None
        else None
    )
    try:
        if job.kind == EDGE_JOB_KIND:
            # 저장된 청크 벡터만으로 계산한다 — 본문을 읽지도, 모델을 부르지도 않는다.
            await finalize_edge_job(conn, job)
            return True
        if job.kind == EXTRACT_JOB_KIND:
            original = await load_original_file(conn, job.document_id)
            if original is None:
                # 문서 삭제 — embed 잡의 같은 경로와 같은 이유로 마감을 시도해 둔다.
                await mark_job_done(conn, job.job_id)
                return True
            filename, data = original
            # OCR은 쪽당 수 초의 CPU 작업이다 — 루프를 막으면 heartbeat가 lease를 연장하지 못한다.
            text = await asyncio.to_thread(ocr_text, data, detect_content_type(filename))
            if lost.is_set():
                logger.warning("lease를 잃어 추출 결과를 버린다 — job_id=%s", job.job_id)
                return True
            await finalize_extract_job(conn, job, text)
            return True
        document = await load_document(conn, job.document_id)
        if document is None:
            # 문서가 삭제됐다 — 실패가 아니다. 잡은 CASCADE로 이미 사라졌으므로 이
            # UPDATE는 0건이지만, 마감을 시도해 두면 "삭제 아닌 이유로 load가 비는"
            # 회귀가 생겨도 잡이 processing으로 방치되지 않는다.
            await mark_job_done(conn, job.job_id)
            return True
        content, content_hash = document
        chunks = chunk_text(content)
        # 동기 CPU 바운드 추론이 이벤트 루프를 막으면 LISTEN 수신·폴링 타이머까지 멈춘다.
        vectors = await asyncio.to_thread(provider.embed, chunks)
        if lost.is_set():
            logger.warning("lease를 잃어 임베딩 결과를 버린다 — job_id=%s", job.job_id)
            return True
        await finalize_job(conn, job, content_hash, chunks, vectors)
    except Exception as exc:
        # 잡 하나의 실패가 워커를 죽이면 안 된다 — 백오프로 재시도를 예약하고 넘어간다.
        logger.exception("잡 처리 실패 — job_id=%s document_id=%s", job.job_id, job.document_id)
        if lost.is_set():
            return True  # 남의 잡에 실패를 기록하지 않는다
        await fail_job(conn, job, exc)
    finally:
        if heartbeat is not None:
            finished.set()
            await _wait_for_heartbeat(heartbeat, job)
    return True


# 기다림을 접은 heartbeat. 참조를 쥐지 않으면 끝나기 전에 가비지 컬렉션될 수 있다.
_detached_heartbeats: set[asyncio.Task] = set()


async def _wait_for_heartbeat(heartbeat: asyncio.Task, job: ClaimedJob) -> None:
    """종료 신호를 받은 heartbeat를 최대 한 lease 기다린다. 넘기면 떼어 두고 돌아간다 (#128).

    정상이면 진행 중이던 연장 한 번만 기다리면 되고, 그 락 대기는 `bound_lock_wait`가 묶는다.
    이 상한은 락이 아닌 이유(응답 없는 네트워크 등)로 멈춘 경우의 방어선이다 — 상한 없이
    기다리면 heartbeat 하나에 워커 전체가 선다(#122 S5a-2). 한 lease인 이유: 그동안 한 번도
    연장하지 못했다면 DB의 lease는 이미 지났고, 기다려서 얻을 것이 없다.

    떼어 둘 때 취소하지 않고, 연결을 밖에서 닫지도 않는다. 취소는 풀 연결을 트랜잭션 중간에
    되돌리고(#110 B-2), 쿼리가 도는 연결을 다른 태스크가 닫는 것은 안전하지 않으며, 서버에
    보내는 쿼리 취소는 OpenProxy 너머에서 실패했다(S5a-2 측정기 로그). 떼어 둔 heartbeat는
    종료 신호를 이미 받았으므로 진행 중인 호출이 끝나면 멈추고, 그 호출이 lock_timeout·
    keepalive 오류로 끝나면 `openarchive.db.connection`이 오류 난 연결을 풀에 돌려보내지 않고 버린다.
    남의 잡을 늘리지도 못한다 — 연장은 `(id, attempts)`로 자기 선점만 고친다.

    그때까지 떼어 둔 heartbeat는 풀 연결 하나를 쥔다. 락이면 lock_timeout(한 주기) 안에,
    응답 없는 네트워크면 클라이언트 keepalive(`openarchive.db`, 리눅스 약 60초) 안에 풀린다. 그 사이
    떼어 둔 것이 쌓여 풀이 차면 다음 heartbeat는 연결을 못 얻어 실패하고, lease 안에 한 번도
    연장하지 못하면 잡을 잃은 것으로 보고 결과를 버린다 — 워커가 서지는 않는다(ADR-050 트레이드오프 6).
    """
    done, _ = await asyncio.wait({heartbeat}, timeout=get_settings().job_lease_seconds)
    if done:
        heartbeat.result()
        return
    logger.warning(
        "heartbeat가 lease(%s초) 넘게 끝나지 않는다 — 기다리지 않고 떼어 둔다 (job_id=%s)",
        get_settings().job_lease_seconds,
        job.job_id,
    )
    _detached_heartbeats.add(heartbeat)
    heartbeat.add_done_callback(_detached_heartbeats.discard)


async def drain(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    stop: asyncio.Event | None = None,
    lease_conn: LeaseConnection | None = None,
    sweep_interval: float | None = None,
) -> int:
    """잡이 없을 때까지 처리하고 건수를 반환한다 — 폴링 주 경로의 본체 (ADR-009).

    `stop`이 서면 처리 중이던 잡을 마친 뒤 새 잡을 집지 않는다.

    `sweep_interval`이 있으면 잡과 잡 사이에서 그 주기로 좀비를 회수한다 (ADR-050 결정 4).
    루프 머리의 스윕만으로는 drain이 끝날 때까지 회수가 밀린다 — #110 B의 S2-4는 그렇게
    약 13분이 걸렸다. 시작 시점의 스윕은 호출부(run_worker 루프 머리)의 몫이다.
    """
    loop = asyncio.get_running_loop()
    last_sweep = loop.time()
    processed = 0
    while True:
        if sweep_interval is not None and loop.time() - last_sweep >= sweep_interval:
            recovered = await sweep_zombies(conn)
            if recovered:
                logger.info("좀비 잡 %d건을 pending으로 회수", recovered)
            last_sweep = loop.time()
        if not await process_once(conn, provider, stop, lease_conn):
            break
        processed += 1
        if stop is not None and stop.is_set():
            break
    return processed


async def _listen_for_jobs(dsn: str, wake: asyncio.Event) -> None:
    """LISTEN 최적화 — 알림이 오면 다음 폴링을 앞당긴다 (ADR-009).

    어떤 실패도 폴링 주 경로를 막지 않는다. 연결 실패·강제 종료(server_lifetime 등)는
    백오프 후 재등록만 시도하고 워커는 폴링으로 계속 돈다.

    **OpenProxy(6432) 경유에서는 이 최적화가 동작하지 않는다 (2026-08-05 실측).** 프록시가
    알림을 쥐고 있다가 클라이언트가 다음 쿼리를 보낼 때 밀어내므로, 유휴 상태인 이 연결은
    깨어나지 못한다. 노드 직결(로컬 컨테이너·개발)에서는 정상 동작한다. 제거하지 않는 이유는
    직결 환경에서 여전히 유효하고, 실패해도 무해하도록 설계됐기 때문이다.
    그래서 폴링 주기는 5초를 유지한다 — OPENSQL_RESEARCH.md §7-3, ADR-009 재개정.
    """
    delay = 1.0
    while True:
        try:
            # 풀과 같은 keepalive — 유휴로 알림을 기다리는 연결이라 죽은 상대를 스스로
            # 알아챌 방법이 이것뿐이다 (ADR-048 결정 1).
            async with await psycopg.AsyncConnection.connect(
                dsn, autocommit=True, **keepalive_kwargs(dsn)
            ) as conn:
                await conn.execute(f"LISTEN {CHANNEL}")
                logger.info("LISTEN 등록 — 알림이 오면 폴링을 앞당긴다")
                delay = 1.0
                async for _ in conn.notifies():
                    wake.set()
        except Exception:
            # 오류가 아니라 경고다 — LISTEN은 최적화라 없어도 파이프라인은 정상이다.
            logger.warning(
                "LISTEN을 쓸 수 없다 — 폴링만으로 계속한다 (%.0f초 후 재등록 시도)",
                delay,
                exc_info=True,
            )
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60.0)


async def run_worker() -> None:
    """폴링 루프 — 매 주기 좀비 회수 후 잡을 드레인한다. LISTEN은 주기를 앞당길 뿐이다.

    SIGTERM(배포의 `systemctl stop`)과 SIGINT(Ctrl-C)를 받으면 처리 중인 잡을 마치고
    루프를 빠져나온다. 프로세스가 SIGKILL·OOM으로 사라지는 경우는 이 경로를 타지
    못하므로, 그때는 잡이 좀비로 남고 lease가 만료된 뒤 sweep_zombies가 회수한다.
    감독자(systemd)가 워커를 되살리는 것과 이 정상 종료는 구분되어야 한다 — 배포로
    세운 워커를 감독자가 즉시 되살리면 배포가 끝나지 않는다.
    """
    provider = get_provider()
    logger.info(
        "임베딩 워커 기동 — provider=%s, 폴링 주기=%.0fs", provider.name, POLL_INTERVAL_SECONDS
    )
    pool = get_pool()
    await pool.open()
    # 풀을 연 뒤에 예열한다 — API lifespan과 같은 순서다. 반대로 하면 DSN 오설정이
    # 모델 로딩 시간만큼 늦게 드러난다.
    await warm_up(provider)
    wake = asyncio.Event()
    stop = asyncio.Event()

    def request_stop() -> None:
        # wake도 함께 세운다 — 폴링 대기 중이면 남은 주기를 기다리지 않고 즉시 깬다.
        stop.set()
        wake.set()

    loop = asyncio.get_running_loop()
    registered: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        # 시그널 핸들러를 지원하지 않는 환경(윈도우·비메인 스레드)에서는 조용히 건너뛴다.
        # 그 경우 정상 종료 경로가 없을 뿐, 좀비 회수가 여전히 뒤를 받친다.
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_stop)
            registered.append(sig)

    listen_task = asyncio.create_task(_listen_for_jobs(get_settings().database_url, wake))
    try:
        while not stop.is_set():
            try:
                async with pool.connection() as conn:
                    try:
                        # 풀 커넥션은 autocommit이 아니다 — 워커 함수들의 계약에 맞춘다
                        # (모듈 docstring 참조). 풀 설정(openarchive/db.py)은 API와 함께 쓰므로 여기서 켠다.
                        await conn.set_autocommit(True)
                        # 스윕이 루프 머리에 있으므로 첫 반복이 곧 기동 시 1회 스윕이다.
                        recovered = await sweep_zombies(conn)
                        if recovered:
                            logger.info("좀비 잡 %d건을 pending으로 회수", recovered)
                        purged = await purge_expired_idempotency_keys(conn)
                        if purged:
                            logger.info("만료된 멱등키 %d건 정리", purged)
                        processed = await drain(
                            conn,
                            provider,
                            stop,
                            lease_conn=connection,
                            sweep_interval=get_settings().job_lease_seconds,
                        )
                        if processed:
                            logger.info("잡 %d건 처리", processed)
                    except Exception:
                        # 루프가 오류로 끝난 연결은 풀에 돌려보내지 않는다 (ADR-048 결정 2).
                        # `transaction()` 진입 중 서버 오류는 psycopg의 트랜잭션 카운터를
                        # 어긋난 채 IDLE로 남기고, 풀은 그것을 정상으로 받아 이후 빌릴 때마다
                        # AssertionError가 난다(#110 B-2). 요청 경로(openarchive.db.connection)와 달리
                        # DB 오류로 좁히지 않는다 — 잡 처리 중 오염되면 process_once가 그 예외를
                        # 삼키고 fail_job이 AssertionError를 내므로, 루프까지 오는 것은 그쪽이다.
                        await conn.close()
                        raise
            except Exception:
                # 연결 끊김 등 — 처리 중이던 잡은 processing으로 남고 lease 만료 뒤 스윕이 되살린다.
                logger.exception("처리 루프 실패 — 다음 폴링에서 재시도한다")
            if stop.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=POLL_INTERVAL_SECONDS)
            wake.clear()
    finally:
        if stop.is_set():
            logger.info("종료 신호 — 처리 중인 잡을 마치고 멈춘다")
        # 우리가 등록한 것만 되돌린다 — 루프가 이 함수보다 오래 사는 경우(테스트가 그렇다)
        # 이미 끝난 워커의 핸들러가 남아 다음 신호를 가로채지 않게 한다.
        for sig in registered:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
        listen_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listen_task
        await close_pool()


def main() -> None:
    """`python -m openarchive.worker` 진입점. Ctrl-C에 깔끔하게 멈춘다 (ADR-004 별도 프로세스)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("종료 (Ctrl-C)")


if __name__ == "__main__":
    main()
