"""임베딩 워커 (ARCHITECTURE.md "워커 처리 루프", ADR-004·ADR-009·ADR-015).

이 파일이 검증하는 주장은 하나다 — **워커가 몇 개든, 어떤 순서로 죽고 겹치든,
청크의 최종 상태는 문서의 최신 버전으로 수렴한다.** 그 수렴을 만드는 두 장치가
`FOR UPDATE SKIP LOCKED`(안전한 선점)와 커밋 직전 `content_hash` 재확인(낡은 결과
폐기)이며, 둘 다 원리상 Mock으로 확인할 수 없으므로 실제 pgvector 컨테이너에
`backend/migrations/`를 적용한 `migrated_db` 픽스처 위에서 돈다 (CLAUDE.md CRITICAL).

테스트는 `embedding_jobs`에 직접 INSERT하지 않는다 — 문서를 INSERT/UPDATE하면
트리거가 잡을 만든다. 워커 경쟁은 커넥션 두 개(`conn`·`other_conn`)로 재현한다.

시간(좀비 임계·백오프)은 기다리지 않는다 — `started_at`·`next_attempt_at`을 직접
UPDATE해 상황을 만든다. 5분을 기다리는 테스트는 존재할 수 없다.
"""

import asyncio
import contextlib
import threading

import psycopg
import pytest

from app.config import get_settings
from app.db import close_pool
from app.embeddings import FakeProvider
from app.services.chunking import chunk_text
from app.worker import (
    CHANNEL,
    MAX_ATTEMPTS,
    _listen_for_jobs,
    claim_job,
    drain,
    fail_job,
    finalize_job,
    load_document,
    process_once,
    run_worker,
    sweep_zombies,
)

# 청크가 여러 개 나오도록 max_chars(1000)를 넘긴다. 두 판의 어휘를 다르게 두어
# "최종 청크가 어느 판의 내용인가"를 본문으로 판별할 수 있게 한다.
DOC_V1 = "\n\n".join(f"1판 {i}번째 문단. " + "원본과 벡터의 정합성은 DB가 보장한다. " * 20 for i in range(4))
DOC_V2 = "\n\n".join(f"2판 {i}번째 문단. " + "검색되는 청크는 항상 하나의 버전이다. " * 20 for i in range(4))


class ExplodingProvider:
    """embed가 항상 실패하는 프로바이더 — 재시도·백오프·소진 경로 검증용."""

    name = "exploding"
    dimension = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("모델 추론 실패를 재현한다")


class BlockingProvider:
    """embed에서 멈춰 서는 프로바이더 — 워커를 처리 **도중**에 붙잡아 둔다.

    embed는 asyncio.to_thread로 도는 동기 함수라 threading.Event로 막는다.
    """

    name = "blocking"
    dimension = 1024

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.entered.set()
        self.release.wait(timeout=10)
        return FakeProvider().embed(texts)


@pytest.fixture
async def conn(migrated_db: str):
    # autocommit이 워커 함수들의 계약이다 — 아니면 load의 SELECT가 연 암묵 트랜잭션
    # 안에서 이후 transaction() 블록이 SAVEPOINT로 바뀌어 "즉시 커밋"이 사라진다.
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
        yield c


@pytest.fixture
async def other_conn(migrated_db: str):
    """두 번째 워커 역할. 경쟁·동시 수정은 반드시 별도 커넥션이어야 재현된다."""
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as c:
        yield c


async def insert_document(conn, content: str = DOC_V1, content_hash: str = "sha256:v1"):
    """업로드 API가 하는 일과 같다 — INSERT 하나. 잡은 트리거가 만든다."""
    cur = await conn.execute(
        """
        INSERT INTO documents (title, content_type, content, content_hash, owner_id)
        VALUES ('워커 검증 문서', 'md', %s, %s, 'alice')
        RETURNING id
        """,
        (content, content_hash),
    )
    return (await cur.fetchone())[0]


async def edit_document(conn, doc_id, content: str, content_hash: str) -> None:
    """PUT API가 하는 일과 같다 — version+1과 본문 교체. 새 잡은 트리거가 만든다."""
    await conn.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %s, content_hash = %s, updated_at = now()
         WHERE id = %s
        """,
        (content, content_hash, doc_id),
    )


async def job_rows(conn, doc_id) -> list[tuple]:
    """(status, attempts, last_error)를 잡 생성 순서로."""
    cur = await conn.execute(
        "SELECT status, attempts, last_error FROM embedding_jobs WHERE document_id = %s ORDER BY id",
        (doc_id,),
    )
    return await cur.fetchall()


async def chunk_rows(conn, doc_id) -> list[tuple]:
    """(chunk_index, content, version)을 청크 순서로."""
    cur = await conn.execute(
        "SELECT chunk_index, content, version FROM document_chunks"
        " WHERE document_id = %s ORDER BY chunk_index",
        (doc_id,),
    )
    return await cur.fetchall()


async def document_state(conn, doc_id) -> tuple:
    cur = await conn.execute(
        "SELECT version, embedding_status FROM documents WHERE id = %s", (doc_id,)
    )
    return await cur.fetchone()


async def wait_until(predicate, message: str, timeout: float = 20.0) -> None:
    """백그라운드 태스크가 만든 효과를 기다린다 — 조건이 설 때까지 짧게 폴링한다.

    run_worker·_listen_for_jobs는 끝나지 않는 루프라 `await`로 결과를 받을 수 없다.
    관측 가능한 결과가 나타났는지를 바깥에서 확인하는 것이 유일한 방법이다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def test_claim_marks_job_and_document_processing_and_commits(conn, other_conn):
    """claim은 잡·문서를 processing으로 바꾸고 **즉시 커밋**한다.

    다른 커넥션에서 보인다 = 커밋됐다. 임베딩이 오래 걸리는 동안 트랜잭션을 열어두면
    행 잠금이 유지되어 다른 워커가 막히고, processing 배지가 UI에 보이지도 않는다.
    """
    doc_id = await insert_document(conn)

    job = await claim_job(conn)

    assert job is not None
    assert job.document_id == doc_id
    cur = await other_conn.execute(
        "SELECT status, attempts, started_at IS NOT NULL FROM embedding_jobs WHERE id = %s",
        (job.job_id,),
    )
    assert await cur.fetchone() == ("processing", 1, True)
    cur = await other_conn.execute(
        "SELECT embedding_status FROM documents WHERE id = %s", (doc_id,)
    )
    assert (await cur.fetchone())[0] == "processing"


async def test_claim_returns_none_when_there_is_no_ready_job(conn):
    """잡이 없거나, 있어도 `next_attempt_at`이 미래(백오프 예약)면 건드리지 않는다."""
    assert await claim_job(conn) is None

    doc_id = await insert_document(conn)
    await conn.execute(
        "UPDATE embedding_jobs SET next_attempt_at = now() + interval '1 hour'"
        " WHERE document_id = %s",
        (doc_id,),
    )
    assert await claim_job(conn) is None


async def test_two_workers_claim_different_jobs(conn, other_conn):
    """SKIP LOCKED — 잡 2건을 두 워커가 나눠 가진다, 같은 잡을 두 번 집지 않는다.

    A의 claim을 커밋하지 않은 채(외부 트랜잭션) B가 claim해야 잠금 경쟁이 실제로
    일어난다. 커밋해 버리면 B는 "pending이 없어서" 다른 잡을 집는 것뿐이다.
    """
    doc_a = await insert_document(conn, content=DOC_V1, content_hash="sha256:a")
    doc_b = await insert_document(conn, content=DOC_V2, content_hash="sha256:b")

    async with conn.transaction():  # A의 잠금을 쥔 채로 B가 claim한다
        job_a = await claim_job(conn)
        job_b = await asyncio.wait_for(claim_job(other_conn), timeout=5)

    assert job_a is not None and job_b is not None
    assert {job_a.document_id, job_b.document_id} == {doc_a, doc_b}


async def test_a_locked_single_job_is_skipped_not_waited_on(conn, other_conn):
    """잡이 1건뿐이면 늦은 쪽은 **대기하지 않고 즉시 None**을 받는다.

    SKIP LOCKED가 빠지면 B는 A의 커밋까지 잠금 대기한다 — A는 이 테스트 안에서
    커밋하지 않으므로 wait_for(5s)가 터지는 것으로 회귀가 드러난다.
    """
    await insert_document(conn)

    async with conn.transaction():
        assert await claim_job(conn) is not None
        assert await asyncio.wait_for(claim_job(other_conn), timeout=5) is None


async def test_drain_processes_a_new_document_end_to_end(conn):
    """정상 경로 — 업로드(INSERT) 후 drain만으로 청크·상태·잡이 완결된다.

    이 테스트 어디에도 LISTEN이 없다. 폴링 경로 하나로 파이프라인이 동작하는 것이
    ADR-009가 요구하는 성질이다. 청크 내용은 chunk_text 결과와 1:1로 일치해야 하고,
    `document_chunks.version == documents.version`이 정합성 카운터의 전제다.
    """
    doc_id = await insert_document(conn)

    processed = await drain(conn, FakeProvider())

    assert processed == 1
    expected = chunk_text(DOC_V1)
    assert len(expected) > 1  # 다중 청크가 아니면 교체·순서 검증이 무의미하다
    rows = await chunk_rows(conn, doc_id)
    assert [r[0] for r in rows] == list(range(len(expected)))
    assert [r[1] for r in rows] == expected
    assert all(r[2] == 1 for r in rows)
    assert await document_state(conn, doc_id) == (1, "ready")
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]
    cur = await conn.execute(
        "SELECT finished_at IS NOT NULL FROM embedding_jobs WHERE document_id = %s", (doc_id,)
    )
    assert (await cur.fetchone())[0] is True

    # 저장된 벡터가 프로바이더의 결과 그대로다 — 같은 벡터라면 코사인 거리가 0이다.
    vec = FakeProvider().embed([expected[0]])[0]
    literal = "[" + ",".join(map(str, vec)) + "]"
    cur = await conn.execute(
        "SELECT embedding <=> %s::vector FROM document_chunks"
        " WHERE document_id = %s AND chunk_index = 0",
        (literal, doc_id),
    )
    assert (await cur.fetchone())[0] == pytest.approx(0, abs=1e-6)


async def test_reembedding_replaces_chunks_instead_of_accumulating(conn):
    """본문 수정 후 재처리하면 청크는 **누적이 아니라 교체**되고 새 version이 기록된다."""
    doc_id = await insert_document(conn)
    await drain(conn, FakeProvider())

    await edit_document(conn, doc_id, DOC_V2, "sha256:v2")
    processed = await drain(conn, FakeProvider())

    assert processed == 1
    rows = await chunk_rows(conn, doc_id)
    assert [r[1] for r in rows] == chunk_text(DOC_V2)  # v1 잔재가 없다
    assert all(r[2] == 2 for r in rows)
    assert await document_state(conn, doc_id) == (2, "ready")
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "done"]


async def test_finalize_discards_a_stale_result_but_completes_the_job(conn, other_conn):
    """커밋 직전 content_hash 재확인 (ARCHITECTURE 워커 루프 3번).

    처리 도중 문서가 수정됐으면 이 결과는 낡았다 — 청크를 쓰지 않고 폐기하되, 잡은
    done으로 마감한다. 트리거가 만든 새 pending 잡이 최신 내용으로 다시 처리하므로
    실패 처리하면 재시도 횟수만 소모한다.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    content, content_hash = await load_document(conn, job.document_id)
    chunks = chunk_text(content)
    vectors = FakeProvider().embed(chunks)

    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")

    applied = await finalize_job(conn, job, content_hash, chunks, vectors)

    assert applied is False
    assert await chunk_rows(conn, doc_id) == []  # 낡은 청크는 한 줄도 쓰이지 않았다
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]


async def test_competing_workers_converge_on_the_latest_version(conn, other_conn):
    """멀티 워커 수렴 — 이 step에서 가장 중요한 테스트.

    워커A가 v1을 처리하는 사이 문서가 v2가 되고, 워커B가 v2 처리를 **먼저 끝낸 뒤**
    A가 낡은 결과를 커밋하려 한다. A의 결과가 B를 덮어쓰면 낡은 청크가 최종 상태로
    남아 "최신 수렴"(ADR-015)이 무너진다 — content_hash 재확인이 그것을 막는다.
    """
    doc_id = await insert_document(conn)

    # 워커 A: v1 잡을 집어가 본문까지 읽었다 (임베딩이 오래 걸리는 중이라 치자)
    job_a = await claim_job(conn)
    content_a, hash_a = await load_document(conn, job_a.document_id)
    chunks_a = chunk_text(content_a)
    vectors_a = FakeProvider().embed(chunks_a)

    # 그 사이 문서가 v2로 수정된다 — 트리거가 새 pending 잡을 만든다
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")

    # 워커 B: 새 잡으로 v2를 끝까지 처리한다
    job_b = await claim_job(other_conn)
    assert job_b is not None and job_b.job_id != job_a.job_id
    content_b, hash_b = await load_document(other_conn, job_b.document_id)
    chunks_b = chunk_text(content_b)
    vectors_b = FakeProvider().embed(chunks_b)
    assert await finalize_job(other_conn, job_b, hash_b, chunks_b, vectors_b) is True

    # 워커 A가 뒤늦게 낡은 v1 결과를 커밋하려 한다 → 스스로 폐기해야 한다
    assert await finalize_job(conn, job_a, hash_a, chunks_a, vectors_a) is False

    rows = await chunk_rows(conn, doc_id)
    assert [r[1] for r in rows] == chunk_text(DOC_V2)  # 최종 상태는 v2 내용이다
    assert all(r[2] == 2 for r in rows)
    assert await document_state(conn, doc_id) == (2, "ready")
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "done"]


async def test_chunk_version_comes_from_finalize_not_from_load(conn, other_conn):
    """본문이 A → B → A로 돌아오면 content_hash는 원래대로지만 version은 2 올라 있다.

    이때 version을 load 시점에 읽으면 해시 재확인은 통과하는데 청크에는 낡은 version이
    박힌다. 그러면 정합성 검증(`c.version <> d.version`)과 /admin/status 카운터가
    "어긋난 청크가 없다"고 거짓 보고한다 — 지표가 무의미해지는 것이지 에러가 나지 않아
    더 위험하다. 그래서 version은 finalize의 `FOR UPDATE` 아래에서 읽은 값이어야 한다
    (이슈 #6 ⚠️, ARCHITECTURE "워커 처리 루프" 3번).
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    content, content_hash = await load_document(conn, job.document_id)
    version_at_load = (await document_state(conn, doc_id))[0]
    chunks = chunk_text(content)
    vectors = FakeProvider().embed(chunks)

    # 본문이 떠났다가 그대로 돌아온다 — 해시 재확인은 통과하고 version만 2 오른다.
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")
    await edit_document(other_conn, doc_id, DOC_V1, "sha256:v1")

    assert await finalize_job(conn, job, content_hash, chunks, vectors) is True

    version, status = await document_state(conn, doc_id)
    assert (version, status) == (version_at_load + 2, "ready")
    rows = await chunk_rows(conn, doc_id)
    assert [r[1] for r in rows] == chunk_text(DOC_V1)
    assert all(r[2] == version for r in rows)  # load 시점의 version(1)이면 안 된다


async def test_deleting_the_document_mid_processing_is_harmless(conn, other_conn):
    """삭제 정합성 — 처리 도중 문서가 삭제되면 finalize는 예외 없이 0건으로 끝난다.

    잡·청크는 CASCADE가 문서와 원자적으로 지웠으므로 워커가 치울 것이 없다
    (ARCHITECTURE 정합성 보장 표).
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    content, content_hash = await load_document(conn, job.document_id)
    chunks = chunk_text(content)
    vectors = FakeProvider().embed(chunks)

    await other_conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

    assert await load_document(conn, job.document_id) is None
    applied = await finalize_job(conn, job, content_hash, chunks, vectors)

    assert applied is False
    for table in ("document_chunks", "embedding_jobs"):
        cur = await conn.execute(
            f"SELECT count(*) FROM {table} WHERE document_id = %s", (doc_id,)
        )
        assert (await cur.fetchone())[0] == 0


async def test_a_failed_job_backs_off_and_returns_to_pending(conn):
    """실패한 잡은 pending으로 돌아가되 next_attempt_at이 미래다 (지수 백오프).

    documents.embedding_status는 processing으로 남는다 — 재시도 대기 중에도 사용자에게는
    처리 중이 맞고, 상태를 pending으로 돌리는 것은 트리거의 책임이다.
    """
    doc_id = await insert_document(conn)

    assert await process_once(conn, ExplodingProvider()) is True  # 잡을 집었으므로 True

    cur = await conn.execute(
        "SELECT status, attempts, last_error, next_attempt_at > now() FROM embedding_jobs"
        " WHERE document_id = %s",
        (doc_id,),
    )
    status, attempts, last_error, deferred = await cur.fetchone()
    assert (status, attempts, deferred) == ("pending", 1, True)
    assert "모델 추론 실패를 재현한다" in last_error
    assert (await document_state(conn, doc_id))[1] == "processing"


async def test_retries_exhaust_into_error_state(conn):
    """MAX_ATTEMPTS번 실패하면 잡도 문서도 error가 되고, 더는 집히지 않는다.

    백오프를 기다리지 않는다 — 예약 시각(next_attempt_at)을 직접 당겨 재시도를 만든다.
    """
    doc_id = await insert_document(conn)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert await process_once(conn, ExplodingProvider()) is True
        if attempt < MAX_ATTEMPTS:
            await conn.execute(
                "UPDATE embedding_jobs SET next_attempt_at = now() WHERE document_id = %s",
                (doc_id,),
            )

    assert await job_rows(conn, doc_id) == [
        ("error", MAX_ATTEMPTS, "RuntimeError: 모델 추론 실패를 재현한다")
    ]
    assert (await document_state(conn, doc_id))[1] == "error"
    assert await process_once(conn, ExplodingProvider()) is False


async def test_fail_job_yields_to_a_newer_pending_job(conn, other_conn):
    """실패한 잡을 pending으로 되돌리기 전에 새 pending 잡이 생겨 있으면 마감한다.

    문서당 pending 1개(uq_pending_job_per_doc)라 복귀가 유니크 제약에 걸리고, 어차피
    새 잡이 최신 내용으로 처리한다. finalize의 낡은 결과 폐기와 같은 원칙이다 —
    이 분기가 없으면 UniqueViolation이 워커를 죽인다.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)

    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # 새 pending 잡
    await fail_job(conn, job, RuntimeError("낡은 잡의 실패"))

    jobs = await job_rows(conn, doc_id)
    assert [j[0] for j in jobs] == ["done", "pending"]
    assert "낡은 잡의 실패" in jobs[0][2]


async def test_exhausted_retries_yield_to_a_newer_job_without_flagging_an_error(conn, other_conn):
    """재시도가 소진된 시점에 새 pending 잡이 있으면 문서를 error로 떨어뜨리지 않는다.

    이 잡은 낡은 내용을 보고 있었으므로 수명이 끝난 것이지 문서가 실패한 것이 아니다.
    최신 내용은 새 잡이 처리해 곧 ready로 수렴하는데, 소진 검사를 pending 검사보다
    먼저 하면 그 사이 사용자에게 거짓 error 배지가 뜬다.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    # 백오프를 기다리지 않는다 — 소진 직전 상태를 attempts로 직접 만든다.
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s WHERE id = %s", (MAX_ATTEMPTS, job.job_id)
    )
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # 새 pending 잡

    await fail_job(conn, job, RuntimeError("소진 시점의 실패"))

    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]
    assert (await document_state(conn, doc_id))[1] != "error"


async def test_sweep_returns_old_processing_jobs_to_pending(conn):
    """좀비 회수 — 임계(5분)를 넘긴 processing 잡만 pending으로 되돌린다.

    attempts는 초기화하지 않는다 — 매번 초기화하면 계속 죽는 잡이 영원히 재시도되어
    MAX_ATTEMPTS가 무의미해진다.
    """
    stale_doc = await insert_document(conn, content_hash="sha256:stale")
    fresh_doc = await insert_document(conn, content=DOC_V2, content_hash="sha256:fresh")
    stale_job = await claim_job(conn)  # 잡 id 순서상 첫 claim이 stale_doc의 잡이다
    assert stale_job.document_id == stale_doc
    await claim_job(conn)

    # 죽은 워커를 재현한다 — 임계보다 오래 processing인 잡
    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes' WHERE id = %s",
        (stale_job.job_id,),
    )

    assert await sweep_zombies(conn) == 1

    assert await job_rows(conn, stale_doc) == [("pending", 1, None)]  # attempts 유지
    assert [j[0] for j in await job_rows(conn, fresh_doc)] == ["processing"]  # 임계 이내


async def test_sweep_completes_a_zombie_whose_document_moved_on(conn, other_conn):
    """문서가 이미 수정된 좀비는 pending 복귀 대신 done으로 마감한다.

    새 pending 잡이 있어 복귀가 uq_pending_job_per_doc에 걸리고, 그 잡이 최신 내용으로
    처리한다. 반환값은 pending으로 **회수한** 건수만 센다.
    """
    doc_id = await insert_document(conn)
    zombie = await claim_job(conn)
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # 새 pending 잡
    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes' WHERE id = %s",
        (zombie.job_id,),
    )

    assert await sweep_zombies(conn) == 0

    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]


async def test_sweep_waits_for_an_uncommitted_edit_before_deciding(conn, other_conn):
    """좀비 판정도 실패 처리와 똑같이 **문서 행을 잠근 뒤** 한다
    (ARCHITECTURE "워커 처리 루프" 4·5번 공통 예외).

    잠그지 않으면 "새 pending 잡이 있는가"를 statement 스냅샷으로 판정하게 되어, 아직
    커밋되지 않은 수정이 만든 잡을 놓치고 좀비를 pending으로 되돌린다. 그 UPDATE는
    uq_pending_job_per_doc의 미확정 인덱스 항목에서 대기하다가 상대가 커밋되는 순간
    UniqueViolation으로 터지고, run_worker의 except가 그것을 삼켜 그 주기의 drain이
    통째로 스킵된다. test_sweep_completes_a_zombie_whose_document_moved_on은 수정이
    이미 커밋된 뒤라 이 경합을 재현하지 못한다.
    """
    doc_id = await insert_document(conn)
    zombie = await claim_job(conn)
    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes' WHERE id = %s",
        (zombie.job_id,),
    )

    async with other_conn.transaction():
        # 커밋 전이라 스윕의 스냅샷에는 새 잡이 보이지 않는다 — 문서 잠금만이 이것을 막는다.
        await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")
        sweep = asyncio.create_task(sweep_zombies(conn))
        await asyncio.sleep(0.2)  # 스윕이 문서 잠금까지 도달할 시간을 준다

    assert await asyncio.wait_for(sweep, timeout=5) == 0  # 되돌린 것이 없다
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]


async def test_polling_drain_handles_multiple_documents_without_listen(conn):
    """폴링만으로 동작한다 (ADR-009) — drain 한 번이 쌓인 잡을 전부 비운다."""
    ids = [
        await insert_document(conn, content_hash=f"sha256:doc{i}") for i in range(3)
    ]

    assert await drain(conn, FakeProvider()) == 3

    for doc_id in ids:
        assert (await document_state(conn, doc_id))[1] == "ready"
        assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]


async def test_run_worker_drains_a_new_document_end_to_end(migrated_db, conn, monkeypatch):
    """워커 진입점 자체를 태운다 — 풀 배선·기동 시 스윕·드레인·프로바이더 선택까지.

    위 테스트들은 전부 픽스처가 만든 커넥션 위에서 워커 함수를 직접 부른다. run_worker의
    루프와 풀 배선은 여기서만 지나간다. autocommit 계약은 **결과만 봐서는 드러나지
    않는다** — 없어도 풀 반납 시점에 전부 커밋되어 문서는 결국 ready가 된다. 그래서
    아래 test_run_worker_holds_no_open_transaction_while_embedding이 따로 본다.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    get_settings.cache_clear()
    await close_pool()  # 앞선 테스트가 다른 DSN으로 열어둔 풀을 물려받지 않는다

    doc_id = await insert_document(conn)

    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(
            lambda: _is_ready(conn, doc_id),
            message="run_worker가 문서를 ready로 만들지 못했다",
        )
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await close_pool()

    assert [r[1] for r in await chunk_rows(conn, doc_id)] == chunk_text(DOC_V1)
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]


async def test_run_worker_holds_no_open_transaction_while_embedding(
    migrated_db, conn, other_conn, monkeypatch
):
    """워커는 풀 커넥션을 autocommit으로 세운다 — 임베딩 중 열린 트랜잭션이 없어야 한다.

    autocommit이 아니면 load_document의 SELECT가 암묵 트랜잭션을 열고, 그 뒤의
    `transaction()` 블록이 전부 SAVEPOINT로 바뀌어 claim의 "즉시 커밋"이 사라진다.
    그러면 임베딩이 도는 수 초 동안 잡 행 잠금이 유지되어 다른 워커의 claim이 막힌다.
    실제로 그 줄을 지워도 위의 end-to-end 테스트는 통과한다 — 드레인이 끝나면 풀 반납
    시점에 어차피 커밋되기 때문이다. 처리 **도중**의 상태를 봐야만 드러난다.
    """
    provider = BlockingProvider()
    monkeypatch.setattr("app.worker.get_provider", lambda: provider)
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    await close_pool()

    doc_id = await insert_document(conn)

    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(
            lambda: _embedding_started(provider), message="워커가 임베딩에 진입하지 않았다"
        )
        assert await _idle_in_transaction_backends(other_conn) == 0
        # 같은 이유의 관측 가능한 결과 — claim이 이미 커밋되어 다른 커넥션에서 보인다.
        assert [j[0] for j in await job_rows(other_conn, doc_id)] == ["processing"]
    finally:
        provider.release.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await close_pool()


async def test_listen_wakes_the_worker_on_the_trigger_notify(migrated_db, conn):
    """LISTEN 최적화의 **수신** 쪽 (ADR-009). 발행 쪽은 test_triggers.py가 본다.

    깨우기에 실패해도 파이프라인은 폴링으로 계속 동작한다 — 바로 그래서 이 경로가
    조용히 죽어도 다른 어떤 테스트도 붉어지지 않는다.
    """
    wake = asyncio.Event()
    listener = asyncio.create_task(_listen_for_jobs(migrated_db, wake))
    try:
        # 등록 전에 알림을 쏘면 그 알림은 사라진다 — 먼저 등록을 확인한다.
        await wait_until(
            lambda: _listen_is_registered(conn), message="LISTEN이 등록되지 않았다", timeout=10.0
        )
        await insert_document(conn)

        await asyncio.wait_for(wake.wait(), timeout=5)
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener


async def _is_ready(conn, doc_id) -> bool:
    return (await document_state(conn, doc_id))[1] == "ready"


async def _embedding_started(provider: "BlockingProvider") -> bool:
    return provider.entered.is_set()


async def _idle_in_transaction_backends(conn) -> int:
    """열린 트랜잭션을 쥔 채 노는 백엔드 수. 테스트 커넥션은 전부 autocommit이라 0이다."""
    cur = await conn.execute(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND state = 'idle in transaction'"
    )
    return (await cur.fetchone())[0]


async def _listen_is_registered(conn) -> bool:
    """다른 세션이 채널에 LISTEN을 걸었는지 본다 (idle 백엔드의 마지막 쿼리로 판별)."""
    cur = await conn.execute(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND query = %s",
        (f"LISTEN {CHANNEL}",),
    )
    return (await cur.fetchone())[0] > 0
