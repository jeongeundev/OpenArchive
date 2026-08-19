"""임베딩 워커 — DB가 만들어 둔 잡을 집어가는 무상태 실행기 (ARCHITECTURE "워커 처리 루프").

잡 생성·코얼레싱·삭제 정합성은 전부 DB 계층(트리거·파셜 유니크 인덱스·CASCADE)이
보장하므로, 워커의 책임은 둘뿐이다.

1. 잡을 안전하게 집어가기 — `FOR UPDATE SKIP LOCKED`
2. 커밋 직전 "내가 읽은 내용이 아직 최신인가" 확인 — `content_hash` 재확인

2번이 멀티 워커 정합성의 핵심이다. 없으면 두 워커가 경쟁할 때 낡은 버전이 최종
상태로 남아 "최신 수렴"(ADR-015)이 무너진다.

기동은 폴링이 주 경로다. LISTEN/NOTIFY는 지연을 줄이는 최적화일 뿐이며, OpenProxy
경유 동작이 문서로 보장되지 않으므로 실패해도 워커는 폴링으로 계속 돈다 (ADR-009).
마이그레이션은 실행하지 않는다 — 실행 주체는 API 서버 하나다 (ADR-012).

**프로세스의 생사는 이 모듈의 책임이 아니다** (ADR-038). SIGKILL·OOM으로 죽으면
스스로 살아날 수 없고, 배포 호스트에서는 systemd 유닛이 되살린다. 여기서 다루는 것은
그 죽음이 남긴 상태뿐이다: 정상 종료(SIGTERM)는 선점을 반납하고, 비정상 종료가 남긴
좀비는 다음 워커의 `sweep_zombies`가 재시도 예산과 함께 정리한다.

잡 처리 함수들은 커넥션을 인자로 받는다 — 테스트가 커넥션 두 개로 워커 경쟁을
재현하기 위함이다. 커넥션은 **autocommit이어야 한다**: 아니면 load의 SELECT가 연
암묵 트랜잭션 탓에 이후 `transaction()` 블록이 SAVEPOINT로 바뀌어, claim의
"즉시 커밋"이 조용히 사라진다.
"""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.config import get_settings
from app.db import close_pool, get_pool
from app.embeddings import EmbeddingProvider, get_provider
from app.services.chunking import chunk_text
from app.vectors import to_pgvector_literal

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
MAX_ATTEMPTS = 3

# 좀비 회수로 예산을 소진한 잡의 last_error. fail_job이 남기는 `타입: 메시지` 형식을
# 따라, UI가 두 경로의 실패를 같은 모양으로 보여줄 수 있게 한다.
ZOMBIE_EXHAUSTED_ERROR = (
    "WorkerCrashLoop: 워커가 반복적으로 비정상 종료해 재시도 예산을 소진했다"
)

CHANNEL = "embedding_jobs"


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    document_id: UUID


async def claim_job(conn: psycopg.AsyncConnection) -> ClaimedJob | None:
    """pending 잡 하나를 processing으로 선점하고 **즉시 커밋**한다. 없으면 None.

    임베딩은 오래 걸린다 — 트랜잭션을 열어둔 채 처리하면 잡 행 잠금이 유지되어 다른
    워커의 claim이 막히고, processing 배지가 UI에 보이지도 않는다. 그래서 선점만
    커밋하고, 결과 반영은 finalize_job의 별도 트랜잭션이 맡는다.
    """
    async with conn.transaction():
        cur = await conn.execute(
            """
            UPDATE embedding_jobs j
               SET status = 'processing', attempts = attempts + 1, started_at = now()
             WHERE j.id = (SELECT id FROM embedding_jobs
                            WHERE status = 'pending' AND next_attempt_at <= now()
                            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING j.id, j.document_id
            """
        )
        row = await cur.fetchone()
        if row is None:
            return None
        job = ClaimedJob(job_id=row[0], document_id=row[1])

        # SET 절에 content_hash가 없으므로 트리거는 발화하지 않는다 (UI 표시용 전환).
        await conn.execute(
            """
            UPDATE documents SET embedding_status = 'processing'
             WHERE id = %s AND embedding_status <> 'processing'
            """,
            (job.document_id,),
        )
    return job


async def load_document(conn: psycopg.AsyncConnection, document_id: UUID) -> tuple[str, str] | None:
    """(content, content_hash)를 읽는다. 문서가 이미 삭제됐으면 None.

    version은 여기서 읽지 않는다 — 본문이 A → B → A로 되돌아오면 content_hash는
    원래대로지만 version은 2 올라 있어, 해시 재확인은 통과하는데 여기서 읽은 version은
    낡은 값이 된다. version은 finalize_job의 FOR UPDATE 아래에서 읽는다.
    """
    cur = await conn.execute(
        "SELECT content, content_hash FROM documents WHERE id = %s", (document_id,)
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row is not None else None


async def finalize_job(
    conn: psycopg.AsyncConnection,
    job: ClaimedJob,
    expected_hash: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> bool:
    """임베딩 결과를 단일 트랜잭션으로 반영한다. 반영했으면 True, 폐기했으면 False."""
    async with conn.transaction():
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
        current_hash, version = row

        if current_hash != expected_hash:
            # 처리 도중 문서가 수정됐다 — 이 결과는 낡았으므로 폐기한다. 트리거가 만든
            # 새 pending 잡이 최신 내용으로 다시 처리하므로 실패가 아니라 마감이다.
            # 실패 처리하면 재시도 횟수만 소모한다.
            await conn.execute(
                "UPDATE embedding_jobs SET status = 'done', finished_at = now() WHERE id = %s",
                (job.job_id,),
            )
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
        await conn.execute(
            "UPDATE embedding_jobs SET status = 'done', finished_at = now() WHERE id = %s",
            (job.job_id,),
        )
    return True


async def fail_job(conn: psycopg.AsyncConnection, job: ClaimedJob, error: Exception) -> None:
    """실패한 잡을 지수 백오프로 재시도 대기시키거나, 소진되면 error로 마감한다.

    documents.embedding_status는 pending으로 되돌리지 않는다 — 재시도 대기 중에도
    사용자에게는 처리 중이 맞고, 상태를 pending으로 돌리는 것은 트리거의 책임이다.
    """
    message = f"{type(error).__name__}: {error}"
    async with conn.transaction():
        # 문서 행을 먼저 잠근다. 잡 생성은 전부 documents 변경 트리거 안에서 일어나므로,
        # 이 잠금이 아래 "다른 pending 잡이 있는가" 판정과 pending 복귀 사이에 새 잡이
        # 끼어드는 것(uq_pending_job_per_doc 위반)을 막는다.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            return  # 문서 삭제 — 잡도 CASCADE로 소멸했으니 남길 것이 없다

        cur = await conn.execute(
            "SELECT 1 FROM embedding_jobs WHERE document_id = %s AND status = 'pending'",
            (job.document_id,),
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
                   SET status = 'done', last_error = %s, finished_at = now()
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
                   SET status = 'error', last_error = %s, finished_at = now()
                 WHERE id = %s
                """,
                (message, job.job_id),
            )
            await conn.execute(
                "UPDATE documents SET embedding_status = 'error' WHERE id = %s",
                (job.document_id,),
            )
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

    반납하지 않고 죽어도 정합성은 깨지지 않는다. 다만 잡이 좀비로 남아 다음 워커가
    임계(기본 5분)를 기다리게 되므로, 배포마다 그만큼 파이프라인이 늦어진다.
    """
    async with conn.transaction():
        # fail_job·sweep_zombies와 같은 잠금 순서다 — 판정과 기록 사이에 새 pending
        # 잡이 커밋되면 pending 복귀가 uq_pending_job_per_doc 위반으로 터진다.
        cur = await conn.execute(
            "SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (job.document_id,)
        )
        if await cur.fetchone() is None:
            return  # 문서 삭제 — 잡도 CASCADE로 소멸했으니 반납할 곳이 없다

        cur = await conn.execute(
            "SELECT 1 FROM embedding_jobs WHERE document_id = %s AND status = 'pending'",
            (job.document_id,),
        )
        if await cur.fetchone() is not None:
            # 처리 중 문서가 수정됐다 — 새 잡이 최신 내용으로 처리하므로 마감한다.
            await conn.execute(
                "UPDATE embedding_jobs SET status = 'done', finished_at = now() WHERE id = %s",
                (job.job_id,),
            )
            return

        # documents.embedding_status는 되돌리지 않는다 — fail_job과 같은 이유다.
        # 곧 다른 워커가 집어가므로 사용자에게는 처리 중이 맞다.
        await conn.execute(
            "UPDATE embedding_jobs SET status = 'pending', attempts = attempts - 1"
            " WHERE id = %s",
            (job.job_id,),
        )


async def sweep_zombies(conn: psycopg.AsyncConnection) -> int:
    """죽은 워커가 processing으로 방치한 잡을 회수한다. pending으로 복귀시킨 건수 반환.

    반환값은 **회수한 건수만** 센다 — 예산을 소진해 error로 격리한 잡과, 문서가 이미
    수정되어 done으로 마감한 잡은 포함하지 않는다.

    attempts는 초기화하지 않는다 — 매번 초기화하면 계속 죽는 잡이 영원히 재시도된다.
    다만 유지하는 것만으로는 부족하다: `claim_job`은 attempts를 보지 않으므로, 예산을
    실제로 강제하는 것은 아래 error 마감 하나뿐이다. 그것이 없으면 재시도 상한이
    `fail_job`(예외로 잡히는 실패)에만 걸리고, 워커 프로세스를 죽이는 잡은 회수 →
    재선점 → 재크래시를 무한히 반복한다.
    """
    timeout_minutes = get_settings().zombie_timeout_minutes

    async with conn.transaction():
        # 판정 전에 대상 문서 행을 잠근다 — fail_job과 같은 이유다 (ARCHITECTURE 4·5번
        # 공통 예외). 잡 생성은 전부 documents 변경 트리거 안에서 일어나므로, 이 잠금이
        # 아래 두 UPDATE의 (NOT) EXISTS 판정과 상태 기록 사이에 새 pending 잡이 커밋되는
        # 것을 막는다. 잠그지 않으면 READ COMMITTED의 statement 스냅샷 탓에 그 잡을 놓쳐
        # 좀비를 pending으로 되돌리고, uq_pending_job_per_doc 위반으로 스윕이 통째로 터진다.
        # 잡보다 문서를 먼저 잠그는 순서가 문서 수정 트랜잭션과의 교착도 함께 없앤다.
        await conn.execute(
            """
            SELECT 1 FROM documents
             WHERE id IN (SELECT document_id FROM embedding_jobs
                           WHERE status = 'processing'
                             AND started_at < now() - make_interval(mins => %s))
             ORDER BY id
               FOR UPDATE
            """,
            (timeout_minutes,),
        )

        # 문서가 이미 수정되어 새 pending 잡이 있는 좀비는 pending 복귀가 문서당
        # pending 1개 제약에 걸린다 — 새 잡이 최신 내용으로 처리하므로 done으로 마감한다.
        await conn.execute(
            """
            UPDATE embedding_jobs j
               SET status = 'done', finished_at = now()
             WHERE j.status = 'processing'
               AND j.started_at < now() - make_interval(mins => %s)
               AND EXISTS (SELECT 1 FROM embedding_jobs p
                            WHERE p.document_id = j.document_id AND p.status = 'pending')
            """,
            (timeout_minutes,),
        )

        # 재시도 예산을 소진한 좀비는 회수하지 않고 격리한다. 위 done 마감보다 **뒤**에
        # 두는 순서가 fail_job과 같다 — 문서가 이미 수정됐다면 그 잡은 실패한 것이 아니라
        # 수명이 끝난 것이므로 error 배지를 달면 안 된다. NOT EXISTS를 여기서도 명시해
        # 두 UPDATE의 순서에 정합성이 의존하지 않게 한다.
        cur = await conn.execute(
            """
            UPDATE embedding_jobs j
               SET status = 'error', last_error = %s, finished_at = now()
             WHERE j.status = 'processing'
               AND j.started_at < now() - make_interval(mins => %s)
               AND j.attempts >= %s
               AND NOT EXISTS (SELECT 1 FROM embedding_jobs p
                                WHERE p.document_id = j.document_id AND p.status = 'pending')
         RETURNING j.document_id
            """,
            (ZOMBIE_EXHAUSTED_ERROR, timeout_minutes, MAX_ATTEMPTS),
        )
        exhausted = [row[0] for row in await cur.fetchall()]
        if exhausted:
            # fail_job의 소진 처리와 같은 상태로 맞춘다. 청크는 지우지 않으므로 검색은
            # 이전 버전으로 계속되고, 정합성 카운터는 어긋난 채 남는다 — 격리했다고
            # 어긋남을 숨기면 계약이 거짓말이 된다. 재개 수단은 문서 재수정이다
            # (003_triggers.sql의 `SET content_hash = content_hash` 경로).
            await conn.execute(
                "UPDATE documents SET embedding_status = 'error' WHERE id = ANY(%s)",
                (exhausted,),
            )

        cur = await conn.execute(
            """
            UPDATE embedding_jobs j
               SET status = 'pending'
             WHERE j.status = 'processing'
               AND j.started_at < now() - make_interval(mins => %s)
               AND NOT EXISTS (SELECT 1 FROM embedding_jobs p
                                WHERE p.document_id = j.document_id AND p.status = 'pending')
            """,
            (timeout_minutes,),
        )
        return cur.rowcount


async def process_once(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    stop: asyncio.Event | None = None,
) -> bool:
    """잡 하나를 처리한다. 집어간 잡이 있었으면 True, 없으면 False.

    처리 실패도 True다 — fail_job이 재시도를 예약했고, drain의 반복 조건은 "이번에
    할 일이 있었는가"이기 때문이다.

    `stop`은 정상 종료 신호다. **임베딩을 시작하기 전에** 확인해 반납하므로, 배포로
    세운 워커가 잡을 processing으로 붙든 채 사라지지 않는다. 이미 임베딩에 들어간
    잡은 끝까지 처리한다 — 중간에 끊어도 할 수 있는 일이 반납뿐이고, 완료가 더 나은
    결과다. 반납한 주기는 "할 일이 있었다"로 세지 않으므로 False를 돌려준다.
    """
    job = await claim_job(conn)
    if job is None:
        return False
    if stop is not None and stop.is_set():
        await release_job(conn, job)
        return False
    try:
        document = await load_document(conn, job.document_id)
        if document is None:
            # 문서가 삭제됐다 — 실패가 아니다. 잡은 CASCADE로 이미 사라졌으므로 이
            # UPDATE는 0건이지만, 마감을 시도해 두면 "삭제 아닌 이유로 load가 비는"
            # 회귀가 생겨도 잡이 processing으로 방치되지 않는다.
            await conn.execute(
                "UPDATE embedding_jobs SET status = 'done', finished_at = now() WHERE id = %s",
                (job.job_id,),
            )
            return True
        content, content_hash = document
        chunks = chunk_text(content)
        # 동기 CPU 바운드 추론이 이벤트 루프를 막으면 LISTEN 수신·폴링 타이머까지 멈춘다.
        vectors = await asyncio.to_thread(provider.embed, chunks)
        await finalize_job(conn, job, content_hash, chunks, vectors)
    except Exception as exc:
        # 잡 하나의 실패가 워커를 죽이면 안 된다 — 백오프로 재시도를 예약하고 넘어간다.
        logger.exception("잡 처리 실패 — job_id=%s document_id=%s", job.job_id, job.document_id)
        await fail_job(conn, job, exc)
    return True


async def drain(
    conn: psycopg.AsyncConnection,
    provider: EmbeddingProvider,
    stop: asyncio.Event | None = None,
) -> int:
    """잡이 없을 때까지 처리하고 건수를 반환한다 — 폴링 주 경로의 본체 (ADR-009).

    `stop`이 서면 처리 중이던 잡을 마친 뒤 새 잡을 집지 않는다.
    """
    processed = 0
    while await process_once(conn, provider, stop):
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
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
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
    못하므로, 그때는 잡이 좀비로 남고 다음 워커의 sweep_zombies가 임계 후에 회수한다.
    감독자(systemd)가 워커를 되살리는 것과 이 정상 종료는 구분되어야 한다 — 배포로
    세운 워커를 감독자가 즉시 되살리면 배포가 끝나지 않는다.
    """
    provider = get_provider()
    logger.info(
        "임베딩 워커 기동 — provider=%s, 폴링 주기=%.0fs", provider.name, POLL_INTERVAL_SECONDS
    )
    pool = get_pool()
    await pool.open()
    wake = asyncio.Event()
    stop = asyncio.Event()

    def request_stop() -> None:
        # wake도 함께 세운다 — 폴링 대기 중이면 남은 주기를 기다리지 않고 즉시 깬다.
        stop.set()
        wake.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # 시그널 핸들러를 지원하지 않는 환경(윈도우·비메인 스레드)에서는 조용히 건너뛴다.
        # 그 경우 정상 종료 경로가 없을 뿐, 좀비 회수가 여전히 뒤를 받친다.
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_stop)

    listen_task = asyncio.create_task(_listen_for_jobs(get_settings().database_url, wake))
    try:
        while not stop.is_set():
            try:
                async with pool.connection() as conn:
                    # 풀 커넥션은 autocommit이 아니다 — 워커 함수들의 계약에 맞춘다
                    # (모듈 docstring 참조). app/db.py는 수정하지 않는다.
                    await conn.set_autocommit(True)
                    # 스윕이 루프 머리에 있으므로 첫 반복이 곧 기동 시 1회 스윕이다.
                    recovered = await sweep_zombies(conn)
                    if recovered:
                        logger.info("좀비 잡 %d건을 pending으로 회수", recovered)
                    processed = await drain(conn, provider, stop)
                    if processed:
                        logger.info("잡 %d건 처리", processed)
            except Exception:
                # 연결 끊김 등 — 처리 중이던 잡은 processing으로 남고 좀비 스윕이 되살린다.
                logger.exception("처리 루프 실패 — 다음 폴링에서 재시도한다")
            if stop.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=POLL_INTERVAL_SECONDS)
            wake.clear()
    finally:
        if stop.is_set():
            logger.info("종료 신호 — 처리 중인 잡을 마치고 멈춘다")
        listen_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listen_task
        await close_pool()


def main() -> None:
    """`python -m app.worker` 진입점. Ctrl-C에 깔끔하게 멈춘다 (ADR-004 별도 프로세스)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("종료 (Ctrl-C)")


if __name__ == "__main__":
    main()
