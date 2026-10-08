"""미리보기 워커의 판별 커밋·재시도·소유권을 실제 DB로 검증한다."""

import asyncio
import contextlib
import threading

import psycopg
import pytest
from conftest import insert_test_document

from openarchive import worker
from openarchive.embeddings import FakeProvider
from openarchive.services.preview import ConverterUnavailable, PreviewRenderFailed


@pytest.fixture
async def conn(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


async def originals(conn, count=2):
    doc = await insert_test_document(conn, title="preview", content="이전 본문")
    for version in range(1, count + 1):
        await conn.execute(
            "INSERT INTO document_files (document_id, file_version, filename, data, text_version, uploaded_by)"
            " VALUES (%s, %s, %s, %s, 1, 'alice')",
            (doc, version, f"{version}.docx", str(version).encode()),
        )
    await conn.execute("UPDATE embedding_jobs SET status = 'done' WHERE kind <> 'preview'")
    return doc


async def rows(conn):
    return await (
        await conn.execute(
            "SELECT status, pdf, error FROM document_file_previews ORDER BY file_version"
        )
    ).fetchall()


async def job_row(conn):
    return await (
        await conn.execute(
            "SELECT status, attempts, next_attempt_at > now(), last_error"
            " FROM embedding_jobs WHERE kind = 'preview' ORDER BY id DESC LIMIT 1"
        )
    ).fetchone()


async def test_preview_claim_defers_only_preview(conn):
    await originals(conn, 1)
    doc = await insert_test_document(conn, title="later", content="later")
    job = await worker.claim_job(conn)
    assert (job.kind, job.document_id) == ("embed", doc)


async def test_preview_two_plates_and_version_text(conn, monkeypatch):
    doc = await originals(conn)
    await conn.execute(
        "UPDATE documents SET content = '현재 본문', content_hash = 'new' WHERE id = %s", (doc,)
    )
    await conn.execute("UPDATE embedding_jobs SET status = 'done' WHERE kind <> 'preview'")
    await conn.execute("UPDATE document_files SET text_version = NULL WHERE file_version = 2")
    calls = []

    def convert(data, filename, *, document_text):
        calls.append((data, filename, document_text))
        return b"PDF" + data

    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    assert await worker.process_once(conn, FakeProvider())
    assert calls == [(b"1", "1.docx", "이전 본문"), (b"2", "2.docx", "현재 본문")]
    assert await rows(conn) == [("ready", b"PDF1", None), ("ready", b"PDF2", None)]
    assert (await job_row(conn))[:2] == ("done", 1)


@pytest.mark.parametrize(
    "error,status",
    [(ConverterUnavailable("missing"), "unavailable"), (PreviewRenderFailed("bad"), "failed")],
)
async def test_preview_deterministic_failure(conn, monkeypatch, error, status):
    await originals(conn, 1)

    def convert(*args, **kwargs):
        raise error

    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    await worker.process_once(conn, FakeProvider())
    assert await rows(conn) == [(status, None, str(error))]
    assert (await job_row(conn))[:2] == ("done", 1)


async def test_preview_partial_commit_retry_only_pending(conn, monkeypatch):
    await originals(conn)
    calls = []

    def convert(data, *args, **kwargs):
        calls.append(data)
        if len(calls) == 2:
            raise RuntimeError("retry")
        return b"PDF" + data

    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    await worker.process_once(conn, FakeProvider())
    assert await rows(conn) == [("ready", b"PDF1", None), ("pending", None, None)]
    assert (await job_row(conn))[:3] == ("pending", 1, True)
    await conn.execute("UPDATE embedding_jobs SET next_attempt_at = now() WHERE kind = 'preview'")
    await worker.process_once(conn, FakeProvider())
    assert calls == [b"1", b"2", b"2"]
    assert (await job_row(conn))[:2] == ("done", 2)


@pytest.mark.parametrize("zombie", [False, True])
async def test_preview_exhaustion_keeps_search_states(conn, zombie):
    doc = await originals(conn)
    before = await (
        await conn.execute(
            "SELECT embedding_status, extraction_status FROM documents WHERE id = %s", (doc,)
        )
    ).fetchone()
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s WHERE kind = 'preview'", (worker.MAX_ATTEMPTS - 1,)
    )
    job = await worker.claim_job(conn)
    if zombie:
        await conn.execute(
            "UPDATE embedding_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (job.job_id,),
        )
        assert await worker.sweep_zombies(conn) == 0
        message = worker.ZOMBIE_EXHAUSTED_ERROR
    else:
        await worker.fail_job(conn, job, RuntimeError("exhausted"))
        message = "RuntimeError: exhausted"
    assert await rows(conn) == [("failed", None, message)] * 2
    assert (await job_row(conn))[0] == "error"
    assert (
        await (
            await conn.execute(
                "SELECT embedding_status, extraction_status FROM documents WHERE id = %s", (doc,)
            )
        ).fetchone()
        == before
    )


@pytest.mark.parametrize("delete", [False, True])
async def test_preview_lost_ownership_or_deleted_during_conversion(
    conn,
    migrated_db,
    monkeypatch,
    delete,
):
    doc = await originals(conn)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def convert(data, *args, **kwargs):
        calls.append(data)
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release converter")
        return b"PDF"

    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    task = asyncio.create_task(worker.process_once(conn, FakeProvider()))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        # 별도 연결이 문서·잡을 바꿀 수 있어야 한다 — 변환 중 잠금을 쥐지 않는다.
        async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as other:
            await other.execute("SET lock_timeout = '1s'")
            if delete:
                await other.execute("DELETE FROM documents WHERE id = %s", (doc,))
            else:
                await other.execute(
                    "UPDATE embedding_jobs SET attempts = attempts + 1 WHERE kind = 'preview'"
                )
    finally:
        release.set()
        assert await asyncio.wait_for(task, 15)
    assert calls == [b"1"]
    assert await rows(conn) == ([] if delete else [("pending", None, None)] * 2)
    if not delete:
        assert (await job_row(conn))[0] == "processing"


async def test_preview_lost_event_discards_result(conn, migrated_db, monkeypatch):
    monkeypatch.setenv("JOB_LEASE_SECONDS", "1")
    await originals(conn)
    entered, release = threading.Event(), threading.Event()
    loss_observed = asyncio.Event()
    extend_lease = worker.extend_lease
    calls = []

    async def observe_lease(hb, job):
        owned = await extend_lease(hb, job)
        if not owned:
            loss_observed.set()
        return owned

    def convert(data, *args, **kwargs):
        calls.append(data)
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release converter")
        return b"PDF"

    monkeypatch.setattr(worker, "extend_lease", observe_lease)
    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as hb:

        @contextlib.asynccontextmanager
        async def lease_conn():
            yield hb

        task = asyncio.create_task(worker.process_once(conn, FakeProvider(), lease_conn=lease_conn))
        try:
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.01)
            await hb.execute("UPDATE embedding_jobs SET status = 'pending' WHERE kind = 'preview'")
            await asyncio.wait_for(loss_observed.wait(), 5)
        finally:
            release.set()
            assert await asyncio.wait_for(task, 15)
    assert calls == [b"1"]
    assert await rows(conn) == [("pending", None, None)] * 2
    assert (await job_row(conn))[0] == "pending"


async def test_preview_claim_preserves_other_kinds_fifo(conn):
    await originals(conn, 1)
    expected = []
    for kind in ("edges", "extract", "embed"):
        doc = await insert_test_document(conn, title=kind, content=kind)
        # 트리거가 만든 잡의 종류만 바꿔 선점 순서 입력을 구성한다.
        await conn.execute(
            "UPDATE embedding_jobs SET kind = %s WHERE document_id = %s", (kind, doc)
        )
        expected.append((kind, doc))
    for kind, doc in expected:
        job = await worker.claim_job(conn)
        assert (job.kind, job.document_id) == (kind, doc)
    assert (await worker.claim_job(conn)).kind == "preview"


async def test_preview_first_failure_leaves_all_plates_pending(conn, monkeypatch):
    await originals(conn)

    def convert(*args, **kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(worker, "convert_to_pdf", convert)
    await worker.process_once(conn, FakeProvider())
    assert await rows(conn) == [("pending", None, None)] * 2
    assert await job_row(conn) == ("pending", 1, True, "TimeoutError: timeout")


@pytest.mark.parametrize("zombie", [False, True])
async def test_preview_exhaustion_preserves_ready_plate(conn, zombie):
    await originals(conn)
    await conn.execute(
        "UPDATE document_file_previews SET status = 'ready', pdf = %s WHERE file_version = 1",
        (b"saved",),
    )
    await conn.execute(
        "UPDATE embedding_jobs SET attempts = %s WHERE kind = 'preview'",
        (worker.MAX_ATTEMPTS - 1,),
    )
    job = await worker.claim_job(conn)
    if zombie:
        await conn.execute(
            "UPDATE embedding_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (job.job_id,),
        )
        await worker.sweep_zombies(conn)
    else:
        await worker.fail_job(conn, job, RuntimeError("exhausted"))
    result = await rows(conn)
    assert result[0] == ("ready", b"saved", None)
    assert result[1][0] == "failed"
