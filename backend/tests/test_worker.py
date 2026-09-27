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
import logging
import os
import signal
import threading
from typing import Self

import psycopg
import pytest

from app.config import get_settings
from app.db import close_pool
from app.embeddings import FakeProvider
from app.services.chunking import chunk_text
from app.worker import (
    CHANNEL,
    MAX_ATTEMPTS,
    ZOMBIE_EXHAUSTED_ERROR,
    _listen_for_jobs,
    claim_job,
    drain,
    fail_job,
    finalize_edge_job,
    finalize_job,
    load_document,
    process_once,
    purge_expired_idempotency_keys,
    release_job,
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

    기동 시 예열(`warm_up`)은 막지 않고 통과시킨다. 여기서 붙잡으려는 것은 잡을
    처리하는 워커인데, 예열까지 막으면 잡을 집기도 전에 멈춰 서서 `entered`가
    "임베딩에 진입했다"를 더 이상 뜻하지 못한다.

    "첫 호출이 예열"이라는 가정의 근거는 두 테스트다 — 잡 없이도 예열이 일어남은
    test_run_worker_warms_up_the_model_before_taking_any_job이, 그것이 **한 번뿐**임은
    test_main.py의 test_startup_warms_up_the_embedding_provider가 고정한다.
    """

    name = "blocking"
    dimension = 1024

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.warmed_up = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.warmed_up:
            self.warmed_up = True
            return FakeProvider().embed(texts)
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


async def job_rows(conn, doc_id, kind: str = "embed") -> list[tuple]:
    """(status, attempts, last_error)를 잡 생성 순서로. **한 종류만** 본다.

    ready 전이가 관계 잡(kind='edges')을 만들므로, 종류를 고정하지 않으면 임베딩 잡의
    수명을 보는 단언에 관계 잡이 섞여 들어와 "잡이 몇 건인가"가 무의미해진다. 관계 잡은
    같은 헬퍼에 kind='edges'로 따로 본다.
    """
    cur = await conn.execute(
        "SELECT status, attempts, last_error FROM embedding_jobs"
        " WHERE document_id = %s AND kind = %s ORDER BY id",
        (doc_id, kind),
    )
    return await cur.fetchall()


async def edge_count(conn, doc_id) -> int:
    """이 문서가 **계산한** 관계 수 — 저장은 단방향이라 src 기준이다 (ADR-029 개정)."""
    cur = await conn.execute(
        "SELECT count(*) FROM document_edges WHERE src_document_id = %s", (doc_id,)
    )
    return (await cur.fetchone())[0]


async def break_edge_rebuild(conn) -> None:
    """관계 판정을 항상 실패하게 만든다 — 관계 잡의 실패 경로 검증용.

    원복하지 않는다. clean_db 픽스처가 매 테스트마다 public 스키마를 통째로 갈아 끼우므로
    다음 테스트로 새지 않는다 (test_slow_edges 트리거와 같은 방식).
    """
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION rebuild_document_edges(target_document_id uuid)
          RETURNS void LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION '관계 판정 실패를 재현한다'; END $$;
        """
    )


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


async def test_finished_at_records_the_end_of_the_finalize_transaction(conn):
    await insert_document(conn)
    job = await claim_job(conn)
    assert job is not None
    chunks = chunk_text(DOC_V1)
    vectors = FakeProvider().embed(chunks)
    await conn.execute(
        """
        CREATE FUNCTION test_slow_edges() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$;
        CREATE TRIGGER trg_test_slow_edges AFTER UPDATE OF embedding_status ON documents
          FOR EACH ROW WHEN (NEW.embedding_status = 'ready')
          EXECUTE FUNCTION test_slow_edges();
        """
    )

    cur = await conn.execute("SELECT clock_timestamp()")
    (before,) = await cur.fetchone()
    assert await finalize_job(conn, job, "sha256:v1", chunks, vectors) is True

    cur = await conn.execute(
        "SELECT finished_at FROM embedding_jobs WHERE id = %s", (job.job_id,)
    )
    (finished_at,) = await cur.fetchone()
    assert (finished_at - before).total_seconds() >= 0.3


async def test_fail_job_finished_at_is_the_actual_time(conn):
    doc_id = await insert_document(conn)
    for _ in range(MAX_ATTEMPTS - 1):
        assert await process_once(conn, ExplodingProvider()) is True
        await conn.execute(
            "UPDATE embedding_jobs SET next_attempt_at = now() WHERE document_id = %s",
            (doc_id,),
        )
    job = await claim_job(conn)
    assert job is not None

    # 기준 시각 전에 트랜잭션을 열어 now()라면 반드시 기준보다 이르게 만든다.
    async with conn.transaction():
        cur = await conn.execute("SELECT clock_timestamp()")
        (before,) = await cur.fetchone()
        await fail_job(conn, job, RuntimeError("모델 추론 실패를 재현한다"))

    cur = await conn.execute(
        "SELECT status, finished_at FROM embedding_jobs WHERE id = %s", (job.job_id,)
    )
    status, finished_at = await cur.fetchone()
    assert status == "error"
    assert finished_at >= before
    assert (await document_state(conn, doc_id))[1] == "error"


async def test_drain_processes_a_new_document_end_to_end(conn):
    """정상 경로 — 업로드(INSERT) 후 drain만으로 청크·상태·잡이 완결된다.

    이 테스트 어디에도 LISTEN이 없다. 폴링 경로 하나로 파이프라인이 동작하는 것이
    ADR-009가 요구하는 성질이다. 청크 내용은 chunk_text 결과와 1:1로 일치해야 하고,
    `document_chunks.version == documents.version`이 정합성 카운터의 전제다.
    """
    doc_id = await insert_document(conn)

    processed = await drain(conn, FakeProvider())

    assert processed == 2  # 임베딩 잡 + 그것이 만든 관계 잡
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


class WireRecorder:
    """클라이언트 → 서버 사이에 끼워, 클라이언트가 보낸 SQL을 순서대로 기록하는 TCP 중계기.

    OpenProxy는 이 메시지 순서만 보고 라우팅한다 — `BEGIN` 안이면 primary, 밖의 SELECT는
    replica (OPENSQL_RESEARCH §6). 로컬 컨테이너에는 프록시가 없으므로, 프록시가 보는 것을
    직접 기록해 검증한다. libpq trace는 리눅스 전용이라 쓸 수 없다.
    """

    def __init__(self, dsn: str) -> None:
        params = psycopg.conninfo.conninfo_to_dict(dsn)
        self._upstream = (params.get("host") or "localhost", int(params.get("port") or 5432))
        self._params = params
        self.sql: list[str] = []

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._relay, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        # SSL·GSS 협상 패킷을 빼야 첫 메시지가 곧 startup이다.
        self.dsn = psycopg.conninfo.make_conninfo(
            **{**self._params, "host": "127.0.0.1", "port": str(port)},
            sslmode="disable",
            gssencmode="disable",
        )
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _relay(self, client_r, client_w) -> None:
        server_r, server_w = await asyncio.open_connection(*self._upstream)

        async def pipe(reader, writer, record: bool) -> None:
            buffer, started = b"", False
            with contextlib.suppress(ConnectionError):
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
                    if not record:
                        continue
                    buffer += data
                    while True:
                        if not started:  # startup 패킷에는 타입 바이트가 없다
                            if len(buffer) < 4 or len(buffer) < int.from_bytes(buffer[:4]):
                                break
                            buffer, started = buffer[int.from_bytes(buffer[:4]) :], True
                            continue
                        if len(buffer) < 5 or len(buffer) < 1 + int.from_bytes(buffer[1:5]):
                            break
                        kind, body = buffer[:1], buffer[5 : 1 + int.from_bytes(buffer[1:5])]
                        buffer = buffer[1 + int.from_bytes(buffer[1:5]) :]
                        if kind == b"Q":  # simple query
                            self.sql.append(body.rstrip(b"\0").decode())
                        elif kind == b"P":  # extended protocol Parse: name\0query\0...
                            self.sql.append(body.split(b"\0")[1].decode())
            writer.close()

        await asyncio.gather(
            pipe(client_r, server_w, record=True), pipe(server_r, client_w, record=False)
        )

    def selects_outside_transaction(self) -> list[str]:
        outside, in_tx = [], False
        for sql in self.sql:
            head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
            if head == "BEGIN":
                in_tx = True
            elif head in ("COMMIT", "ROLLBACK"):
                in_tx = False
            elif head == "SELECT" and not in_tx:
                outside.append(sql)
        return outside


async def test_the_worker_never_reads_outside_a_transaction(conn, migrated_db):
    """HA에서 워커의 읽기는 전부 `BEGIN` 안에 있어야 한다 (#110 C).

    OpenProxy(공식 HA 구성)는 트랜잭션 밖 SELECT를 replica로 보낸다. 복제는 비동기라
    replica가 방금 커밋된 문서를 아직 모를 수 있다 — 20MB bytea 쓰기 부하 중에는 커밋
    200ms 뒤에도 40회 중 13회가 옛 상태를 봤다. 본문을 거기서 읽으면 "삭제됐다"로 잡을
    마감하거나 옛 본문으로 임베딩해 해시 재확인에서 폐기하고, 어느 쪽이든 새 잡이 없으니
    문서가 processing에 영원히 멈춘다. 에러는 없다.

    쓰기(UPDATE)는 검사하지 않는다 — 트랜잭션 밖이어도 primary로 간다.
    """
    await insert_document(conn)

    async with (
        WireRecorder(migrated_db) as wire,
        await psycopg.AsyncConnection.connect(wire.dsn, autocommit=True) as relayed,
    ):
        assert await drain(relayed, FakeProvider()) == 2

    # 중계기가 실제로 워커의 SQL을 봤는지 — 아니면 빈 목록이 무조건 통과한다.
    assert any("FOR UPDATE SKIP LOCKED" in sql for sql in wire.sql)
    assert wire.selects_outside_transaction() == []


async def test_reembedding_replaces_chunks_instead_of_accumulating(conn):
    """본문 수정 후 재처리하면 청크는 **누적이 아니라 교체**되고 새 version이 기록된다."""
    doc_id = await insert_document(conn)
    await drain(conn, FakeProvider())

    await edit_document(conn, doc_id, DOC_V2, "sha256:v2")
    processed = await drain(conn, FakeProvider())

    assert processed == 2  # 임베딩 잡 + 그것이 만든 관계 잡
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


async def test_sweep_uses_zero_timeout_from_settings(conn, monkeypatch):
    """데모 설정 0은 방금 processing이 된 잡도 즉시 회수한다."""
    monkeypatch.setenv("ZOMBIE_TIMEOUT_MINUTES", "0")
    doc_id = await insert_document(conn)
    await claim_job(conn)

    assert await sweep_zombies(conn) == 1
    assert await job_rows(conn, doc_id) == [("pending", 1, None)]


async def test_sweep_keeps_fresh_job_with_default_timeout(conn):
    """기본 5분 임계에서는 방금 processing이 된 잡을 회수하지 않는다."""
    doc_id = await insert_document(conn)
    await claim_job(conn)

    assert await sweep_zombies(conn) == 0
    assert [row[0] for row in await job_rows(conn, doc_id)] == ["processing"]


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


async def test_sweep_errors_out_a_zombie_that_exhausted_its_budget(conn):
    """재시도 예산을 소진한 좀비는 pending으로 되돌리지 않고 error로 격리한다.

    이것이 없으면 재시도 상한이 `fail_job`(예외로 잡히는 실패)에만 걸리고, 워커
    프로세스를 죽이는 잡은 좀비 회수 경로로 무한 재시도된다 — `claim_job`은
    `attempts`를 보지 않기 때문이다. 결과는 `fail_job`의 소진 처리와 같아야 한다:
    잡은 error, 문서 배지도 error.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s, started_at = now() - interval '10 minutes'"
        " WHERE id = %s",
        (MAX_ATTEMPTS, job.job_id),
    )

    assert await sweep_zombies(conn) == 0  # 반환값은 pending으로 **회수한** 건수뿐이다

    status, attempts, last_error = (await job_rows(conn, doc_id))[0]
    assert status == "error"
    assert attempts == MAX_ATTEMPTS  # 소진 사실이 남는다
    # fail_job이 남기는 `타입: 메시지` 형식이어야 한다 — UI가 두 경로의 실패를 같은
    # 모양으로 보여준다는 계약이다. "무엇이든 채워져 있다"로는 그것을 지키지 못한다.
    assert last_error == ZOMBIE_EXHAUSTED_ERROR
    assert (await document_state(conn, doc_id))[1] == "error"


async def test_sweep_recovers_a_zombie_one_attempt_below_the_budget(conn):
    """예산이 남은 좀비는 그대로 회수한다 — 경계값(MAX_ATTEMPTS - 1)을 고정한다."""
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s, started_at = now() - interval '10 minutes'"
        " WHERE id = %s",
        (MAX_ATTEMPTS - 1, job.job_id),
    )

    assert await sweep_zombies(conn) == 1

    assert await job_rows(conn, doc_id) == [("pending", MAX_ATTEMPTS - 1, None)]
    assert (await document_state(conn, doc_id))[1] != "error"


async def test_sweep_completes_an_exhausted_zombie_whose_document_moved_on(conn, other_conn):
    """소진된 좀비라도 문서가 이미 수정됐으면 error가 아니라 done으로 마감한다.

    `fail_job`이 "재시도 소진 검사보다 새 pending 잡 검사를 **먼저**" 두는 것과 같은
    순서다. 뒤집으면 최신 내용으로 처리될 문서가 error 배지를 달고, 새 잡이 ready로
    되돌릴 때까지 거짓 상태가 노출된다.
    """
    doc_id = await insert_document(conn)
    zombie = await claim_job(conn)
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # 새 pending 잡
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s, started_at = now() - interval '10 minutes'"
        " WHERE id = %s",
        (MAX_ATTEMPTS, zombie.job_id),
    )

    assert await sweep_zombies(conn) == 0

    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]
    assert (await document_state(conn, doc_id))[1] != "error"


async def test_sweep_handles_two_processing_zombies_on_one_document(conn, other_conn):
    """한 문서에 좀비가 둘이면 하나만 회수하고 나머지는 done으로 마감한다.

    감독자가 워커를 되살리면서 생기는 경로다. 워커1이 job1을 집고 죽어 좀비가 남은 뒤,
    문서가 수정되어 job2가 생기고, 재기동한 워커2가 job2를 집고 또 죽으면 **같은 문서에
    processing 좀비가 둘** 있게 된다. 둘 다 `NOT EXISTS(pending)`을 만족하므로 한 UPDATE가
    두 행을 pending으로 만들면 uq_pending_job_per_doc 위반으로 스윕 트랜잭션이 통째로
    터진다. 그러면 run_worker의 except가 그것을 삼켜 **그 주기의 drain이 아예 실행되지
    않고**, 매 폴링마다 같은 실패가 반복되어 파이프라인이 영구 정지한다.

    잡에는 페이로드가 없어("이 문서는 재임베딩이 필요하다"는 신호뿐) 어느 것을 남겨도
    결과가 같다. 가장 오래된 것(작은 id)을 남긴다.
    """
    doc_id = await insert_document(conn)
    job1 = await claim_job(conn)
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # job2 pending
    job2 = await claim_job(conn)  # job2도 processing — 이제 좀비 후보가 둘이다
    assert job2 is not None and job2.job_id != job1.job_id
    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes'"
        " WHERE document_id = %s",
        (doc_id,),
    )

    assert await sweep_zombies(conn) == 1  # 회수는 정확히 하나

    rows = await job_rows(conn, doc_id)
    assert [r[0] for r in rows] == ["pending", "done"]  # 오래된 것을 남기고 뒤를 마감
    # 회수된 잡이 다시 집히고, uq 위반 없이 파이프라인이 이어진다
    reclaimed = await claim_job(conn)
    assert reclaimed is not None and reclaimed.job_id == job1.job_id


async def test_pipeline_keeps_draining_while_a_zombie_waits_for_its_timeout(conn, other_conn):
    """좀비가 임계를 기다리는 동안에도 다른 pending 잡은 계속 처리된다.

    재기동한 워커는 좀비를 **즉시** 회수하지 않는다 — 죽음을 시간으로 판정하므로
    임계(기본 5분)를 기다려야 한다. 이 대기가 파이프라인 전체를 멈추지 않는다는 것이
    이 테스트의 주장이다: 좀비는 `processing`이라 `claim_job`의 대상이 아니고, 워커는
    남은 pending 잡을 그대로 집어간다. 크래시의 영향은 그 잡 하나로 격리된다.
    """
    zombie_doc = await insert_document(conn, content_hash="sha256:zombie")
    zombie = await claim_job(conn)  # 이 잡을 쥔 워커가 죽었다
    assert zombie.document_id == zombie_doc

    healthy_a = await insert_document(other_conn, content=DOC_V2, content_hash="sha256:a")
    healthy_b = await insert_document(other_conn, content=DOC_V1, content_hash="sha256:b")

    # 재기동한 워커의 첫 스윕 — 임계가 아직 안 지나 좀비는 회수 대상이 아니다
    assert await sweep_zombies(conn) == 0

    processed = await drain(conn, FakeProvider())

    assert processed == 4  # 좀비를 뺀 나머지가 전부 처리됐다 (문서 2건 × 잡 2종)
    assert (await document_state(conn, healthy_a))[1] == "ready"
    assert (await document_state(conn, healthy_b))[1] == "ready"
    assert [j[0] for j in await job_rows(conn, zombie_doc)] == ["processing"]  # 좀비는 그대로


async def test_an_edge_job_is_processed_in_its_own_transaction(conn):
    """관계 판정은 임베딩과 **다른 트랜잭션**에서 일어난다 (ADR-029 결정 3 개정).

    임베딩 잡이 끝난 시점에 관계가 아직 없고, 관계 잡을 처리한 뒤에야 생긴다는 것이
    분리의 관측 가능한 형태다. 관계 잡 처리에 `ExplodingProvider`를 넘기는 이유는
    **프로바이더를 부르지 않음**을 함께 고정하기 위해서다 — 저장된 청크 벡터만으로
    계산하므로 모델이 필요 없고, 부른다면 이 테스트가 붉어진다.
    """
    await insert_document(conn, content=DOC_V2, content_hash="sha256:neighbor")
    await drain(conn, FakeProvider())  # 관계가 생길 수 있도록 이웃 문서를 먼저 ready로
    doc_id = await insert_document(conn)

    assert await process_once(conn, FakeProvider()) is True  # 임베딩 잡

    assert (await document_state(conn, doc_id))[1] == "ready"
    assert len(await chunk_rows(conn, doc_id)) > 0
    assert await edge_count(conn, doc_id) == 0  # ready 전이는 잡만 만든다 (017)
    assert [j[0] for j in await job_rows(conn, doc_id, kind="edges")] == ["pending"]

    assert await process_once(conn, ExplodingProvider()) is True  # 관계 잡

    assert await edge_count(conn, doc_id) > 0
    assert [j[0] for j in await job_rows(conn, doc_id, kind="edges")] == ["done"]
    cur = await conn.execute(
        "SELECT finished_at IS NOT NULL FROM embedding_jobs"
        " WHERE document_id = %s AND kind = 'edges'",
        (doc_id,),
    )
    assert (await cur.fetchone())[0] is True


async def test_a_failing_edge_job_leaves_chunks_and_ready_intact(conn):
    """**이 phase의 핵심 주장** — 관계 판정이 실패해도 청크와 ready는 남는다.

    008까지는 판정이 `finalize_job`과 같은 트랜잭션이라 실패가 임베딩까지 롤백시켰다:
    관계 하나 때문에 문서가 검색에서 통째로 빠진다. 잡으로 분리하면 관계만 재시도되고
    검색은 방금 만든 청크로 계속된다.
    """
    doc_id = await insert_document(conn)
    assert await process_once(conn, FakeProvider()) is True
    chunks_before = await chunk_rows(conn, doc_id)
    assert len(chunks_before) > 0
    await break_edge_rebuild(conn)

    assert await process_once(conn, ExplodingProvider()) is True  # 관계 잡 — 판정이 터진다

    assert await chunk_rows(conn, doc_id) == chunks_before  # 청크는 한 줄도 사라지지 않았다
    assert (await document_state(conn, doc_id))[1] == "ready"
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]  # 임베딩 잡도 그대로
    status, attempts, last_error = (await job_rows(conn, doc_id, kind="edges"))[0]
    assert (status, attempts) == ("pending", 1)  # 백오프 후 재시도
    # 프로바이더 예외(RuntimeError)가 아니라 판정 예외여야 한다 — 관계 잡은 모델을 부르지 않는다.
    assert "관계 판정 실패를 재현한다" in last_error


async def test_an_exhausted_edge_job_does_not_mark_the_document_as_error(conn):
    """관계 잡이 재시도를 소진해도 documents.embedding_status는 ready 그대로다.

    임베딩 잡의 소진은 문서를 error로 떨어뜨린다(test_retries_exhaust_into_error_state).
    관계 잡은 다르다 — 청크가 멀쩡해 검색이 되는데 화면에 "임베딩 실패" 배지가 뜨면
    상태 표시가 거짓말이 된다. 관계 미반영은 별도 카운터가 관측한다.
    """
    doc_id = await insert_document(conn)
    assert await process_once(conn, FakeProvider()) is True
    await break_edge_rebuild(conn)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert await process_once(conn, ExplodingProvider()) is True
        if attempt < MAX_ATTEMPTS:
            await conn.execute(
                "UPDATE embedding_jobs SET next_attempt_at = now()"
                " WHERE document_id = %s AND kind = 'edges'",
                (doc_id,),
            )

    status, attempts, _ = (await job_rows(conn, doc_id, kind="edges"))[0]
    assert (status, attempts) == ("error", MAX_ATTEMPTS)
    assert (await document_state(conn, doc_id))[1] == "ready"
    assert len(await chunk_rows(conn, doc_id)) > 0
    assert await process_once(conn, FakeProvider()) is False  # 더는 집히지 않는다


async def test_an_edge_job_judges_with_the_chunks_that_exist_even_during_reembedding(
    conn, other_conn
):
    """재임베딩이 시작된 문서라도 관계 잡은 **지금 있는 청크로** 판정하고 마감한다.

    판정 없이 폐기하지 않는 이유는 폐기의 전제가 거짓이기 때문이다. "새 ready 전이가
    새 관계 잡을 만든다"는 재임베딩이 성공할 때만 참이고, 재시도 예산을 소진해 error로
    끝나면 ready 전이가 영영 오지 않는다. 그러면 관계를 한 번도 계산하지 않은 문서가
    남는데 관계 미반영 카운터는 잡이 done이므로 0을 보고한다.

    지금 계산한 관계가 곧 교체될 청크 기준이어도 손해는 판정 한 번뿐이다 — 재임베딩이
    끝나면 그 ready 전이의 새 잡이 다시 계산한다 (ADR-029 결정 3 개정).
    """
    await insert_document(conn, content=DOC_V2, content_hash="sha256:neighbor")
    await drain(conn, FakeProvider())  # 판정이 돌면 관계가 생길 이웃을 둔다
    doc_id = await insert_document(conn)
    assert await process_once(conn, FakeProvider()) is True  # ready + 관계 잡 pending

    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")  # 재임베딩 시작

    job = await claim_job(conn)
    assert job is not None and job.kind == "edges" and job.document_id == doc_id
    assert await finalize_edge_job(conn, job) is True

    assert await edge_count(conn, doc_id) > 0  # 판정이 돌았다
    assert [j[0] for j in await job_rows(conn, doc_id, kind="edges")] == ["done"]
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]


async def test_edges_remain_when_a_reembedding_ends_in_error(conn, other_conn):
    """재임베딩이 error로 끝나도 그 문서에는 현재 청크 기준의 관계가 남는다.

    관계 미반영 카운터는 "관계 잡이 done이 아닌 문서"를 센다. 그 0이 참이려면 **done이
    된 잡은 반드시 판정을 돌렸어야** 한다. 판정 없이 마감하는 경로가 하나라도 있으면
    여기서 관계가 빈 채로 카운터가 0이 되고, 정합성 지표가 거짓말이 된다.
    """
    await insert_document(conn, content=DOC_V2, content_hash="sha256:neighbor")
    await drain(conn, FakeProvider())
    doc_id = await insert_document(conn)
    assert await process_once(conn, FakeProvider()) is True  # ready + 관계 잡 pending
    chunks_before = await chunk_rows(conn, doc_id)

    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")
    assert await process_once(conn, FakeProvider()) is True  # 잡 id 순 — 관계 잡이 먼저다

    for attempt in range(1, MAX_ATTEMPTS + 1):  # 재임베딩이 예산을 소진한다
        assert await process_once(conn, ExplodingProvider()) is True
        if attempt < MAX_ATTEMPTS:
            await conn.execute(
                "UPDATE embedding_jobs SET next_attempt_at = now()"
                " WHERE document_id = %s AND kind = 'embed' AND status = 'pending'",
                (doc_id,),
            )

    assert (await document_state(conn, doc_id))[1] == "error"
    assert await chunk_rows(conn, doc_id) == chunks_before  # 청크는 1판 그대로다
    assert await edge_count(conn, doc_id) > 0  # 그 청크 기준의 관계도 남아 있다
    assert [j[0] for j in await job_rows(conn, doc_id, kind="edges")] == ["done"]


async def test_sweep_recovers_both_job_kinds(conn):
    """좀비 회수는 종류를 가리지 않는다 — 임베딩 잡도 관계 잡도 pending으로 돌아온다."""
    edge_doc = await insert_document(conn, content=DOC_V2, content_hash="sha256:edges")
    assert await process_once(conn, FakeProvider()) is True  # edge_doc의 관계 잡이 대기한다
    embed_doc = await insert_document(conn, content_hash="sha256:embed")

    edge_zombie = await claim_job(conn)  # 잡 id 순 — 관계 잡이 먼저다
    assert edge_zombie is not None and edge_zombie.kind == "edges"
    assert edge_zombie.document_id == edge_doc
    embed_zombie = await claim_job(conn)
    assert embed_zombie is not None and embed_zombie.kind == "embed"
    assert embed_zombie.document_id == embed_doc
    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes'"
        " WHERE id = ANY(%s)",
        ([edge_zombie.job_id, embed_zombie.job_id],),
    )

    assert await sweep_zombies(conn) == 2

    assert await job_rows(conn, edge_doc, kind="edges") == [("pending", 1, None)]
    assert await job_rows(conn, embed_doc) == [("pending", 1, None)]


async def test_sweep_recovers_both_kinds_of_zombie_on_one_document(conn, other_conn):
    """**한 문서**에 임베딩 좀비와 관계 좀비가 같이 있어도 둘 다 회수한다.

    rn > 1을 done으로 마감하는 규칙은 같은 (문서, 종류) 안에서만 성립한다. 종류를 빼고
    문서로만 줄을 세우면 id가 큰 쪽 — 여기서는 새로 생긴 임베딩 잡 — 이 rn=2가 되어
    아무 이유 없이 done으로 마감된다. 그러면 문서는 수정됐는데 재임베딩이 영영 일어나지
    않고, 청크가 옛 내용에 머문 채 `ready` 배지만 남는다.

    좀비 둘을 서로 다른 문서에 두면 이 규칙을 지나친다 — 문서가 다르면 종류를 빼도 각자
    rn=1이라 통과한다. 그래서 **한 문서에** 두 종류를 만든다.
    """
    doc_id = await insert_document(conn, content=DOC_V2, content_hash="sha256:both-kinds")
    assert await process_once(conn, FakeProvider()) is True  # 임베딩 → ready → 관계 잡
    edge_zombie = await claim_job(conn)
    assert edge_zombie is not None and edge_zombie.kind == "edges"

    await edit_document(other_conn, doc_id, DOC_V2 + " 수정", "sha256:both-kinds-v2")
    embed_zombie = await claim_job(conn)
    assert embed_zombie is not None and embed_zombie.kind == "embed"
    assert embed_zombie.document_id == doc_id  # 같은 문서에 두 종류의 processing 잡

    await conn.execute(
        "UPDATE embedding_jobs SET started_at = now() - interval '10 minutes'"
        " WHERE document_id = %s AND status = 'processing'",
        (doc_id,),
    )

    assert await sweep_zombies(conn) == 2  # 종류마다 하나씩, 둘 다 회수된다

    assert await job_rows(conn, doc_id, kind="edges") == [("pending", 1, None)]
    # 임베딩 잡은 둘이다 — 최초 적재분(정상 done)과 수정으로 생겨 회수된 것.
    assert await job_rows(conn, doc_id) == [("done", 1, None), ("pending", 1, None)]


async def test_sweep_isolates_an_exhausted_edge_zombie_without_flagging_the_document(conn):
    """소진된 관계 좀비는 error로 격리하되 문서 배지는 건드리지 않는다.

    워커를 반복적으로 죽이는 관계 잡도 예산을 소진하면 멈춰야 하지만(그 상한을 실제로
    강제하는 것이 이 격리다), 그 격리가 멀쩡한 청크를 가진 문서에 실패 배지를 달아서는
    안 된다 — 위 소진 테스트의 좀비 경로판이다.
    """
    doc_id = await insert_document(conn)
    assert await process_once(conn, FakeProvider()) is True
    zombie = await claim_job(conn)
    assert zombie is not None and zombie.kind == "edges"
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s, started_at = now() - interval '10 minutes'"
        " WHERE id = %s",
        (MAX_ATTEMPTS, zombie.job_id),
    )

    assert await sweep_zombies(conn) == 0

    assert await job_rows(conn, doc_id, kind="edges") == [
        ("error", MAX_ATTEMPTS, ZOMBIE_EXHAUSTED_ERROR)
    ]
    assert (await document_state(conn, doc_id))[1] == "ready"


async def test_drain_processes_the_edge_job_after_the_embedding_job(conn):
    """drain 한 번이 임베딩 잡과 그것이 만든 관계 잡을 **잡 id 순서대로** 모두 비운다.

    우선순위가 없다는 것이 여기서 결과로 드러난다. 관계 잡을 먼저 집는 큐였다면 doc_a의
    판정이 doc_b가 ready가 되기 전에 돌아 이웃을 찾지 못한다.
    """
    doc_a = await insert_document(conn, content_hash="sha256:a")
    doc_b = await insert_document(conn, content=DOC_V2, content_hash="sha256:b")

    processed = await drain(conn, FakeProvider())

    assert processed == 4  # 임베딩 2건 + 관계 2건
    for doc_id in (doc_a, doc_b):
        assert (await document_state(conn, doc_id))[1] == "ready"
        assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]
        assert [j[0] for j in await job_rows(conn, doc_id, kind="edges")] == ["done"]
        assert await edge_count(conn, doc_id) > 0


async def test_release_returns_the_job_and_refunds_the_attempt(conn):
    """정상 종료(SIGTERM)의 반납 — pending 복귀 + attempts 원복.

    배포로 워커를 세우는 것은 잡의 잘못이 아니다. 원복하지 않으면 배포를
    MAX_ATTEMPTS번 반복하는 것만으로 멀쩡한 문서가 error로 격리된다.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    assert (await job_rows(conn, doc_id))[0][:2] == ("processing", 1)

    await release_job(conn, job)

    assert await job_rows(conn, doc_id) == [("pending", 0, None)]
    # 반납은 실패가 아니다 — 백오프 없이 즉시 다시 집힌다
    reclaimed = await claim_job(conn)
    assert reclaimed is not None and reclaimed.job_id == job.job_id


async def test_release_completes_the_job_when_the_document_moved_on(conn, other_conn):
    """반납 대상 문서에 이미 새 pending 잡이 있으면 done으로 마감한다.

    pending 복귀는 uq_pending_job_per_doc에 걸린다 — fail_job·sweep_zombies와 같은 처리다.
    """
    doc_id = await insert_document(conn)
    job = await claim_job(conn)
    await edit_document(other_conn, doc_id, DOC_V2, "sha256:v2")

    await release_job(conn, job)

    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done", "pending"]


async def test_drain_releases_the_claimed_job_when_shutdown_is_requested(conn):
    """종료 신호를 받으면 새로 집은 잡을 반납하고 드레인을 멈춘다.

    임베딩을 **시작하기 전에** 신호를 확인하므로, 배포로 세운 워커가 잡을 processing으로
    붙든 채 사라지지 않는다 — 다음 워커가 좀비 임계 5분을 기다릴 필요가 없어진다.
    """
    doc_a = await insert_document(conn, content_hash="sha256:a")
    doc_b = await insert_document(conn, content=DOC_V2, content_hash="sha256:b")
    stop = asyncio.Event()
    stop.set()

    processed = await drain(conn, FakeProvider(), stop=stop)

    assert processed == 0  # 하나도 완료하지 않았다
    assert await job_rows(conn, doc_a) == [("pending", 0, None)]  # 집었다가 반납 + 원복
    # doc_b는 **집히지도 않았다.** status·attempts로는 이것을 구분할 수 없다 — 반납이
    # attempts를 원복하므로 claim+release한 잡도 ("pending", 0)으로 남는다. claim만이
    # started_at을 채우고 반납은 그것을 되돌리지 않으므로, 그 컬럼이 유일한 판별자다.
    cur = await conn.execute(
        "SELECT status, attempts, started_at IS NULL FROM embedding_jobs WHERE document_id = %s",
        (doc_b,),
    )
    assert await cur.fetchall() == [("pending", 0, True)]
    cur = await conn.execute(
        "SELECT started_at IS NOT NULL FROM embedding_jobs WHERE document_id = %s", (doc_a,)
    )
    assert (await cur.fetchone())[0] is True  # 대조: doc_a는 집혔다가 반납됐다


async def test_polling_drain_handles_multiple_documents_without_listen(conn):
    """폴링만으로 동작한다 (ADR-009) — drain 한 번이 쌓인 잡을 전부 비운다."""
    ids = [
        await insert_document(conn, content_hash=f"sha256:doc{i}") for i in range(3)
    ]

    assert await drain(conn, FakeProvider()) == 6  # 문서 3건 × (임베딩 + 관계)

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


async def test_run_worker_stops_itself_on_sigterm(migrated_db, conn, monkeypatch):
    """SIGTERM에 루프를 빠져나와 **스스로** 종료한다 — 배포의 `systemctl stop` 경로다.

    이 경로가 없으면 감독자가 워커를 세울 때마다 진행 중이던 잡이 좀비로 남아, 다음
    워커가 임계(기본 5분)를 기다려야 처리가 이어진다. `worker.cancel()`로는 검증할 수
    없다 — 취소는 바깥에서 태스크를 죽이는 것이고, 여기서 확인할 것은 워커가 자기
    판단으로 루프를 빠져나오는가다. 그래서 취소하지 않고 끝나기를 기다린다.

    run_worker가 SIGTERM 핸들러를 등록하므로 이 프로세스는 신호에 죽지 않는다. 등록
    **전에** 발사하면 기본 동작이 살아 있어 pytest가 통째로 종료되므로, 워커가 한 건을
    실제로 처리한 것을 확인한 뒤에 보낸다 — 그 시점이면 등록이 끝나 있다.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    get_settings.cache_clear()
    await close_pool()

    doc_id = await insert_document(conn)
    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(
            lambda: _is_ready(conn, doc_id),
            message="워커가 기동해 한 건을 처리하지 못했다 — 신호를 보낼 시점이 아니다",
        )

        os.kill(os.getpid(), signal.SIGTERM)

        # shield로 감싸 타임아웃이 태스크를 취소하지 않게 한다 — 취소로 끝나면 이 테스트가
        # 증명하려는 것(스스로 종료)이 사라진다. 정리는 아래 finally가 맡는다.
        await asyncio.wait_for(asyncio.shield(worker), timeout=15)
        assert worker.done()
        assert worker.exception() is None  # 예외로 죽은 것이 아니라 정상 반환이어야 한다
    finally:
        if not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await close_pool()


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


async def test_run_worker_warms_up_the_model_before_taking_any_job(
    migrated_db, monkeypatch, recording_provider
):
    """기동 직후 모델을 한 번 예열한다 — 처리할 잡이 하나도 없어도.

    `LocalProvider`는 첫 `embed()`까지 모델(~2GB) 로딩을 미룬다. 예열이 없으면 그
    로딩이 통째로 **첫 업로드의 지연**이 된다 — 사용자가 문서를 올린 뒤에야 모델을
    받기 시작한다. ADR-003이 "워커 예열로 해결한다"고 적어둔 것이 이 호출이다.

    잡을 넣지 않고 확인하는 이유: 잡이 있으면 그 처리 과정의 `embed`와 구분되지 않아
    예열이 있었는지를 증명할 수 없다. 운영 프로바이더로는 이 차이가 "첫 업로드가
    수십 초 걸린다"로만 드러나는데, 테스트는 `FakeProvider` 위에서 도므로 지연이
    아니라 호출을 센다.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    await close_pool()  # 앞선 테스트가 다른 DSN으로 열어둔 풀을 물려받지 않는다
    monkeypatch.setattr("app.worker.get_provider", lambda: recording_provider)

    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(
            lambda: _warmed_up(recording_provider),
            message="잡이 없는데도 예열이 일어나지 않았다 — 첫 업로드가 모델 로딩을 기다린다",
        )
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await close_pool()


async def test_run_worker_survives_a_failed_warmup(
    migrated_db, conn, monkeypatch, warmup_failing_provider
):
    """예열이 실패해도 워커는 계속 돈다 — 예열은 최적화이지 새 실패 지점이 아니다.

    모델을 못 받는 상황(의존성 미설치·다운로드 실패)에서 워커가 기동조차 못 하면
    감독자가 되살리는 부팅 루프가 된다. 삼키고 계속하면 **기존 실패 경로가 더 나은
    진단을 준다** — 잡을 집어 `fail_job`이 `last_error`에 이유를 남기고 문서가
    error로 격리되므로, 사용자가 화면에서 원인을 본다.

    `_listen_for_jobs`가 LISTEN 실패를 다루는 것과 같은 원칙이다 (ADR-009).
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    await close_pool()
    provider = warmup_failing_provider
    monkeypatch.setattr("app.worker.get_provider", lambda: provider)

    doc_id = await insert_document(conn)

    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(
            lambda: _is_ready(conn, doc_id),
            message="예열 실패가 워커를 세웠다 — 이후 잡이 처리되지 않는다",
        )
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await close_pool()

    assert provider.failed_once  # 예열이 실제로 실패한 상태를 지나왔다
    assert [j[0] for j in await job_rows(conn, doc_id)] == ["done"]


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


async def _warmed_up(provider) -> bool:
    return bool(provider.calls)


async def cancel_until_done(task: asyncio.Task) -> None:
    """태스크가 끝날 때까지 취소를 되풀이한다.

    psycopg_pool은 대여 시점 확인(`check_connection`) 중에 난 `CancelledError`를 확인 실패로
    다룬다 — 연결을 반납하고 다음 연결을 빌려 계속한다(`_getconn_with_check_loop`). 취소가
    그 순간에 닿으면 삼켜지고 run_worker는 다음 주기를 돈다. 폴링 주기를 0.02초로 줄인
    테스트에서는 이 겹침이 잦아(12회 중 3회) 한 번만 취소하고 기다리면 끝나지 않는다.
    배포의 워커는 취소가 아니라 SIGTERM으로 멈추므로 이 경로를 타지 않는다.
    """
    while not task.done():
        task.cancel()
        await asyncio.wait([task], timeout=0.5)


def _all_servers_down() -> bytes:
    """OpenProxy가 배정할 서버가 없을 때 돌려주는 응답 — ErrorResponse 뒤 ReadyForQuery(유휴).

    SQLSTATE 58 계열이라 psycopg에서 `psycopg.errors.SystemError`로 올라온다(#110 B 워커 로그의
    "could not get connection from the pool - AllServersDown"과 같은 모양).
    """
    fields = b"".join(
        code + value + b"\0"
        for code, value in (
            (b"S", b"ERROR"),
            (b"V", b"ERROR"),
            (b"C", b"58000"),
            (b"M", b"could not get connection from the pool - AllServersDown"),
        )
    ) + b"\0"
    return b"E" + (len(fields) + 4).to_bytes(4) + fields + b"Z" + (5).to_bytes(4) + b"I"


class BeginFailer:
    """`arm()` 뒤 `skip`개를 흘려보낸 다음 `BEGIN` 하나에 서버 대신 AllServersDown을 돌려주는 TCP 중계기 (#110 B-2).

    OpenProxy(transaction 모드)는 `BEGIN`을 받는 순간 백엔드를 배정하고, 배정할 서버가 없으면
    그 `BEGIN`에 오류를 돌려주되 클라이언트 연결은 유휴로 남긴다. psycopg의 `transaction()`은
    `BEGIN`을 보내기 **전에** 트랜잭션 카운터를 올리므로, 이 오류가 `__aenter__`에서 터지면
    카운터가 되돌려지지 않은 채 연결이 IDLE로 풀에 돌아간다. 풀은 IDLE만 보고 정상으로
    받아들이고, 그 연결의 다음 `transaction()`마다 `AssertionError`가 난다. 로컬 컨테이너에는
    이 오류를 낼 프록시가 없으므로 그 응답만 흉내 낸다 — 나머지는 전부 실제 서버로 흘린다.
    """

    def __init__(self, dsn: str) -> None:
        params = psycopg.conninfo.conninfo_to_dict(dsn)
        self._upstream = (params.get("host") or "localhost", int(params.get("port") or 5432))
        self._params = params
        self._armed = False
        self._skip = 0
        self.injected = 0

    def arm(self, skip: int = 0) -> None:
        self._armed, self._skip = True, skip

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._relay, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.dsn = psycopg.conninfo.make_conninfo(
            **{**self._params, "host": "127.0.0.1", "port": str(port)},
            sslmode="disable",
            gssencmode="disable",
        )
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _relay(self, client_r, client_w) -> None:
        server_r, server_w = await asyncio.open_connection(*self._upstream)

        async def upstream() -> None:
            with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
                head = await client_r.readexactly(4)  # startup 패킷에는 타입 바이트가 없다
                server_w.write(head + await client_r.readexactly(int.from_bytes(head) - 4))
                while True:
                    kind = await client_r.readexactly(1)
                    size = await client_r.readexactly(4)
                    body = await client_r.readexactly(int.from_bytes(size) - 4)
                    begin = kind == b"Q" and body.lstrip().upper().startswith(b"BEGIN")
                    if self._armed and begin and self._skip:
                        self._skip -= 1
                    elif self._armed and begin:
                        self._armed = False
                        self.injected += 1
                        client_w.write(_all_servers_down())
                        await client_w.drain()
                        continue
                    server_w.write(kind + size + body)
                    await server_w.drain()
            server_w.close()

        async def downstream() -> None:
            with contextlib.suppress(ConnectionError):
                while data := await server_r.read(65536):
                    client_w.write(data)
                    await client_w.drain()
            client_w.close()

        await asyncio.gather(upstream(), downstream())


@pytest.mark.parametrize(
    ("skip", "expected_errors"),
    [
        # 스윕의 BEGIN — HA 실측에서 난 자리다. 오류가 곧장 루프로 올라온다.
        (0, [psycopg.errors.SystemError]),
        # 스윕·claim 다음의 load BEGIN — process_once가 첫 오류를 잡아 fail_job으로 넘기고,
        # 오염된 연결 위의 fail_job이 AssertionError를 낸다. 루프에 오는 것은 DB 오류가
        # 아니므로, 루프가 DB 오류일 때만 연결을 버리면 이 경로가 새어 나간다.
        (2, [psycopg.errors.SystemError, AssertionError]),
    ],
    ids=["sweep", "job"],
)
async def test_a_connection_broken_by_a_failed_begin_does_not_return_to_the_pool(
    migrated_db, conn, monkeypatch, caplog, skip, expected_errors
):
    """처리 루프가 오류로 끝나면 그 연결은 풀에 남지 않는다 (ADR-048 결정 2, #110 B-2).

    HA 실측에서 OpenProxy가 스윕의 `BEGIN`에 AllServersDown을 돌려준 뒤, 트랜잭션 카운터가
    어긋난 연결이 풀로 돌아가 이후 그 연결을 빌릴 때마다 `AssertionError`가 났다 — 한때
    풀 4개 중 3개가 오염됐다. 장애는 한 번인데 오류가 끝없이 반복된다. 풀은 연결을 돌려
    쓰므로(먼저 반납된 것부터) 수십 주기면 오염된 연결을 반드시 다시 빌린다. 그래서 주입한
    장애 한 번이 낸 오류 외에는 없어야 한다.
    """
    await insert_document(conn)  # 첫 주기가 잡을 집어 BEGIN 순서가 스윕·claim·load로 정해진다
    real_sweep = sweep_zombies
    sweeps = 0

    async def counting_sweep(conn):
        nonlocal sweeps
        sweeps += 1
        return await real_sweep(conn)

    async def swept_enough() -> bool:
        return sweeps >= 30

    monkeypatch.setattr("app.worker.sweep_zombies", counting_sweep)
    monkeypatch.setattr("app.worker.POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    caplog.set_level(logging.ERROR, logger="app.worker")

    async with BeginFailer(migrated_db) as relay:
        monkeypatch.setenv("DATABASE_URL", relay.dsn)
        get_settings.cache_clear()
        await close_pool()
        relay.arm(skip)
        worker = asyncio.create_task(run_worker())
        try:
            await wait_until(swept_enough, message="워커가 스윕 주기를 충분히 돌지 못했다")
        finally:
            await cancel_until_done(worker)
            await close_pool()

    # 주입이 실제로 일어났는지 — 아니면 오류 0건으로 무조건 통과한다.
    assert relay.injected == 1
    errors = [r.exc_info[1] for r in caplog.records if r.name == "app.worker" and r.exc_info]
    assert [type(e) for e in errors] == expected_errors


async def test_the_listen_connection_uses_keepalive(migrated_db, monkeypatch):
    """LISTEN 연결도 죽은 상대를 수십 초 안에 감지해야 한다 (ADR-048 결정 1, #110 B-1).

    VIP가 원래 노드로 돌아가는 순간 응답을 기다리던 연결은 FIN도 RST도 받지 못한다. OS
    기본 keepalive로는 약 2시간 뒤에야 끊김을 안다. 실제로 연결을 열어 libpq가 받아들인
    값을 읽는다 — 인자만 넘기고 libpq가 모르는 키면 연결 자체가 실패한다.
    """
    opened: list[psycopg.AsyncConnection] = []
    real_connect = psycopg.AsyncConnection.connect

    async def recording_connect(*args, **kwargs):
        conn = await real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    async def listening() -> bool:
        return bool(opened)

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", recording_connect)
    task = asyncio.create_task(_listen_for_jobs(migrated_db, asyncio.Event()))
    try:
        await wait_until(listening, message="LISTEN 연결이 열리지 않았다")
        options = {o.keyword: o.val for o in opened[0].pgconn.info}
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert options[b"keepalives_idle"] == b"30"
    assert options[b"keepalives_interval"] == b"10"
    assert options[b"keepalives_count"] == b"3"
    assert options[b"tcp_user_timeout"] == b"60000"


async def insert_idempotency_key(conn, doc_id, key: str, age: str) -> None:
    await conn.execute(
        """
        INSERT INTO idempotency_keys (owner_id, key, request_hash, document_id, created_at)
        VALUES ('alice', %s, 'sha256:request', %s, now() - %s::interval)
        """,
        (key, doc_id, age),
    )


async def idempotency_keys(conn) -> list[str]:
    cur = await conn.execute("SELECT key FROM idempotency_keys ORDER BY key")
    return [row[0] for row in await cur.fetchall()]


async def test_purge_removes_only_idempotency_keys_older_than_24_hours(conn):
    """키는 24시간 보관한다 (ADR-047 결정 3). 지워지는 것은 키이고 문서는 남는다."""
    doc_id = await insert_document(conn)
    await insert_idempotency_key(conn, doc_id, "expired", "24 hours 1 second")
    await insert_idempotency_key(conn, doc_id, "fresh", "23 hours 59 minutes")

    assert await purge_expired_idempotency_keys(conn) == 1

    assert await idempotency_keys(conn) == ["fresh"]
    assert await document_state(conn, doc_id) is not None


async def test_run_worker_purges_expired_idempotency_keys_on_its_sweep(
    migrated_db, conn, monkeypatch
):
    """별도 스케줄러 없이 워커 스윕 주기에 얹는다 — 기동 시 첫 스윕에서 지워져야 한다."""
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    get_settings.cache_clear()
    await close_pool()

    doc_id = await insert_document(conn)
    await insert_idempotency_key(conn, doc_id, "expired", "2 days")

    async def purged() -> bool:
        return await idempotency_keys(conn) == []

    worker = asyncio.create_task(run_worker())
    try:
        await wait_until(purged, message="워커 스윕이 만료 키를 지우지 않았다")
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await close_pool()
