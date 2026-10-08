import asyncio
import hashlib
from pathlib import Path

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from test_triggers import (
    edges_for,
    insert_document,
    insert_preview_original,
    mark_document_ready,
    unit_vector,
)

from openarchive.embeddings import FakeProvider
from openarchive.services.documents import DocumentNotFound, OriginalFileMissing, create_document
from openarchive.services.system import (
    enqueue_edge_rebuild,
    enqueue_preview_rebuild,
    get_system_status,
    reextract_all,
    reextract_one,
    wait_for_edge_jobs,
)
from openarchive.worker import drain, process_once

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def system_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(
        migrated_db, autocommit=True
    ) as conn:
        yield conn


async def status_of(conn, *, job_lease_seconds: int = 60):
    return await get_system_status(
        conn,
        job_lease_seconds=job_lease_seconds,
        embedding_provider="fake",
    )


async def test_empty_database_has_no_jobs_or_finished_job(system_conn):
    result = await get_system_status(
        system_conn,
        job_lease_seconds=60,
        embedding_provider="fake",
    )

    assert (result.preview_pending, result.preview_failed, result.preview_unavailable) == (0, 0, 0)
    assert result.jobs.pending == 0
    assert result.jobs.processing == 0
    assert result.jobs.recovery_pending == 0
    assert result.jobs.error == 0
    assert result.last_job_finished_at is None


async def test_pending_job_becomes_finished_after_embedding(system_conn):
    await insert_test_document(
        system_conn,
        title="시스템 상태",
        content="OpenSQL 운영 상태를 관측한다.",
    )

    pending = await get_system_status(
        system_conn,
        job_lease_seconds=60,
        embedding_provider="fake",
    )
    assert pending.jobs.pending == 1

    await process_all_embedding_jobs(system_conn, FakeProvider())

    completed = await get_system_status(
        system_conn,
        job_lease_seconds=60,
        embedding_provider="fake",
    )
    assert completed.jobs.pending == 0
    assert completed.last_job_finished_at is not None


async def test_status_includes_values_supplied_by_the_caller(system_conn):
    result = await get_system_status(
        system_conn,
        job_lease_seconds=17,
        embedding_provider="test-provider",
    )

    assert result.job_lease_seconds == 17
    assert result.embedding_provider == "test-provider"


async def test_empty_database_has_no_stale_edges(system_conn):
    assert (await status_of(system_conn)).stale_edge_documents == 0


async def test_stale_edge_documents_counts_documents_whose_edge_job_is_not_done(system_conn):
    """세는 근거는 관계 잡의 상태다 — `document_edges` 행 수가 아니다.

    이웃이 없어 edge가 0행인 문서와 아직 판정하지 않은 문서는 행 수로 구분되지 않는다.
    """
    await insert_test_document(
        system_conn,
        title="관계 판정 대상",
        content="관계가 아직 반영되지 않은 문서.",
    )
    # 임베딩 전에는 관계 잡 자체가 없다 — ready 전이가 만든다 (017).
    assert (await status_of(system_conn)).stale_edge_documents == 0

    # 잡 하나만 처리한다. 임베딩 잡이 끝나 ready가 된 직후이고 관계 잡은 아직 pending이다.
    assert await process_once(system_conn, FakeProvider())
    assert (await status_of(system_conn)).stale_edge_documents == 1

    assert await process_once(system_conn, FakeProvider())
    assert (await status_of(system_conn)).stale_edge_documents == 0


async def test_stale_edge_documents_includes_failed_edge_jobs(system_conn):
    """관계 잡이 error로 격리돼도 계속 센다.

    격리했다고 어긋남을 숨기면 계약이 거짓말이 된다 — 재시도 예산을 소진한 잡을 error로
    격리하면서도 청크를 지우지 않고 정합성 카운터를 어긋난 채 남기는 것과 같은 원칙이다
    (`sweep_zombies`의 소진 처리, ADR-038).
    """
    await insert_test_document(
        system_conn,
        title="관계 판정 실패",
        content="관계 판정이 재시도 예산을 소진한 문서.",
    )
    assert await process_once(system_conn, FakeProvider())
    await system_conn.execute(
        "UPDATE embedding_jobs SET status = 'error' WHERE kind = 'edges'"
    )

    assert (await status_of(system_conn)).stale_edge_documents == 1


async def test_job_counters_only_count_embedding_jobs(system_conn):
    """잡 카운터는 임베딩 잡만 센다 — 관계 잡이 섞이면 "대기 중 임베딩"이 부풀어 보인다."""
    await insert_test_document(
        system_conn,
        title="잡 종류 분리",
        content="임베딩 잡과 관계 잡을 가른다.",
    )
    assert await process_once(system_conn, FakeProvider())
    after_embedding = await status_of(system_conn)
    embed_finished_at = after_embedding.last_job_finished_at
    assert embed_finished_at is not None
    assert after_embedding.jobs.pending == 0
    assert after_embedding.stale_edge_documents == 1

    await system_conn.execute(
        """
        UPDATE embedding_jobs
           SET status = 'processing', started_at = now() - interval '10 minutes',
               lease_expires_at = now() - interval '1 second'
         WHERE kind = 'edges'
        """
    )
    while_processing = await status_of(system_conn)
    assert while_processing.jobs.processing == 0
    assert while_processing.jobs.recovery_pending == 0

    await system_conn.execute(
        "UPDATE embedding_jobs SET status = 'error' WHERE kind = 'edges'"
    )
    assert (await status_of(system_conn)).jobs.error == 0

    await system_conn.execute(
        """
        UPDATE embedding_jobs
           SET status = 'done', finished_at = now() + interval '1 hour'
         WHERE kind = 'edges'
        """
    )
    assert (await status_of(system_conn)).last_job_finished_at == embed_finished_at


# ── 관계 전량 재계산 (openarchive rebuild-edges, #156) ─────────────────────
# 판정은 워커 하나가 한다. 이 명령은 ready 문서마다 관계 잡을 걸고 워커가 그 잡들을 비울 때까지
# 기다린다. 테스트에서는 워커 대신 같은 처리 함수(`drain`)를 다른 연결로 돌린다.


@pytest.fixture
async def worker_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        yield conn


async def test_edge_rebuild_lets_earlier_documents_see_later_ones(
    system_conn, worker_conn, migrated_db
):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        first = insert_document(setup)
        mark_document_ready(setup, first, ["first"], vectors=[unit_vector(0)])
        second = insert_document(setup)
        mark_document_ready(setup, second, ["second"], vectors=[unit_vector(0)])
        # 워커가 적재 순서대로 판정한 상태 — 먼저 들어온 문서는 나중 문서를 못 봤다.
        setup.execute("DELETE FROM embedding_jobs WHERE kind = 'edges'")
        setup.execute("SELECT rebuild_document_edges(%s)", (second,))
        before = edges_for(setup, first)
        assert [(row[0], row[1]) for row in before] == [(second, first)]

        queued = await enqueue_edge_rebuild(system_conn)
        assert queued.documents == 2
        assert edges_for(setup, first) == before  # 거는 것은 판정하지 않는다 — 워커의 일이다

        await drain(worker_conn, FakeProvider())
        assert await wait_for_edge_jobs(system_conn, queued) == 0
        rebuilt = edges_for(setup, first)
        assert {(row[0], row[1]) for row in rebuilt} == {(first, second), (second, first)}


async def test_edge_rebuild_skips_documents_that_are_not_ready(system_conn, migrated_db):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        ready = insert_document(setup)
        mark_document_ready(setup, ready, ["ready"], vectors=[unit_vector(0)])
        insert_document(setup)

    assert (await enqueue_edge_rebuild(system_conn)).documents == 1


async def test_waiting_ends_when_the_worker_empties_the_queued_jobs(
    system_conn, worker_conn, migrated_db
):
    """대기는 워커가 처리할 때까지 끝나지 않고, 진행을 (처리한 수, 전체)로 알린다."""
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        for index in range(3):
            doc_id = insert_document(setup)
            mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(index)])
        # 관계 잡까지 끝난 상태 — 기다릴 잡은 전부 이 요청이 새로 건 것이어야 한다.
        # 대기 중인 잡과 코얼레싱되면 상한을 잘못 잡아도 이 테스트가 통과해 버린다.
        setup.execute("UPDATE embedding_jobs SET status = 'done'")
    queued = await enqueue_edge_rebuild(system_conn)
    progress = []

    waiting = asyncio.create_task(
        wait_for_edge_jobs(
            system_conn, queued, poll_interval=0.01,
            on_progress=lambda done, total: progress.append((done, total)),
        )
    )
    await asyncio.sleep(0.1)
    assert not waiting.done()  # 워커가 아직 아무것도 처리하지 않았다

    assert await drain(worker_conn, FakeProvider()) == 3
    assert await asyncio.wait_for(waiting, timeout=5) == 0
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)


async def test_waiting_does_not_wait_for_jobs_queued_after_the_request(
    system_conn, worker_conn, migrated_db
):
    """요청 뒤에 생긴 잡까지 기다리면 적재가 계속되는 동안 명령이 끝나지 않는다."""
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        doc_id = insert_document(setup)
        mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(0)])
        queued = await enqueue_edge_rebuild(system_conn)
        await drain(worker_conn, FakeProvider())
        later = insert_document(setup)
        mark_document_ready(setup, later, ["later"], vectors=[unit_vector(1)])

    assert await asyncio.wait_for(wait_for_edge_jobs(system_conn, queued), timeout=5) == 0


async def test_waiting_tells_once_when_nothing_moves_and_can_time_out(system_conn, migrated_db):
    """워커가 없으면 잡은 줄지 않는다 — 기다리는 이유를 한 번 알리고, 상한이 있으면 끝낸다."""
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        doc_id = insert_document(setup)
        mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(0)])
    queued = await enqueue_edge_rebuild(system_conn)
    stalls = []

    with pytest.raises(TimeoutError):
        await wait_for_edge_jobs(
            system_conn, queued, poll_interval=0.01, stall_after=0.05, timeout=0.5,
            on_stall=lambda: stalls.append(True),
        )

    assert stalls == [True]


async def test_waiting_reports_documents_whose_edge_judgement_is_isolated(
    system_conn, migrated_db
):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        doc_id = insert_document(setup)
        mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(0)])
        queued = await enqueue_edge_rebuild(system_conn)
        # 워커가 재시도 예산을 소진한 것과 같은 상태
        setup.execute("UPDATE embedding_jobs SET status = 'error' WHERE kind = 'edges'")

    assert await wait_for_edge_jobs(system_conn, queued) == 1


async def test_isolated_count_skips_documents_the_rebuild_does_not_target(
    system_conn, migrated_db
):
    """ready가 아닌 문서는 관계 잡이 다시 걸리지 않아 격리 잡이 닫힐 길이 없다.

    세면 `rebuild-edges`가 매번 실패하고 "다시 실행하세요"가 거짓이 된다. 그 문서의 관계는
    다음 ready 전이에서 트리거가 거는 잡이 맞춘다 (017).
    """
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        doc_id = insert_document(setup)
        mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(0)])
        setup.execute("UPDATE embedding_jobs SET status = 'error' WHERE kind = 'edges'")
        # 재임베딩이 실패해 ready에서 빠진 문서
        setup.execute("UPDATE documents SET embedding_status = 'error' WHERE id = %s", (doc_id,))
    queued = await enqueue_edge_rebuild(system_conn)

    assert await asyncio.wait_for(wait_for_edge_jobs(system_conn, queued), timeout=5) == 0


async def test_progress_never_goes_below_zero_when_an_earlier_job_commits_late(
    system_conn, migrated_db
):
    """잡 id는 커밋 순서가 아니다 — 상한 아래 id가 첫 폴링 뒤에 커밋되면 남은 수가 늘어난다."""
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        doc_id = insert_document(setup)
        mark_document_ready(setup, doc_id, ["text"], vectors=[unit_vector(0)])
        queued = await enqueue_edge_rebuild(system_conn)
        # 상한을 넉넉히 둬 나중에 생긴 잡이 "늦게 커밋된 작은 id"처럼 보이게 한다.
        request = type(queued)(documents=queued.documents, last_job_id=queued.last_job_id + 1000)
        progress = []

        def on_progress(done: int, total: int) -> None:
            progress.append((done, total))
            if len(progress) == 1:
                late = insert_document(setup)
                mark_document_ready(setup, late, ["late"], vectors=[unit_vector(1)])
            else:
                setup.execute("UPDATE embedding_jobs SET status = 'done' WHERE kind = 'edges'")

        assert await asyncio.wait_for(
            wait_for_edge_jobs(system_conn, request, poll_interval=0.01, on_progress=on_progress),
            timeout=5,
        ) == 0

    assert len(progress) >= 3
    assert all(0 <= done <= total for done, total in progress)


async def test_edge_rebuild_recovers_an_isolated_edge_job_and_the_counter(
    system_conn, worker_conn
):
    """격리된 관계 잡의 복구 경로가 이 명령이다 (OPERATIONS) — 다시 걸고 판정이 성공하면 카운터가 0이 된다.

    관계 잡이 재시도 예산을 소진해 error로 격리되면 그 문서는 관계 미반영 수에 계속
    세어진다(의도된 동작이다 — 격리했다고 어긋남을 숨기지 않는다). 다시 건 잡이 성공하면
    워커가 격리된 잡을 마감한다 (`test_worker.py`).
    """
    await insert_test_document(
        system_conn,
        title="관계 복구",
        content="관계 판정이 재시도 예산을 소진해 격리된 문서.",
    )
    assert await process_once(system_conn, FakeProvider())  # 임베딩 잡 → ready + 관계 잡
    await system_conn.execute(
        "UPDATE embedding_jobs SET status = 'error', last_error = 'x' WHERE kind = 'edges'"
    )
    assert (await status_of(system_conn)).stale_edge_documents == 1

    queued = await enqueue_edge_rebuild(system_conn)
    assert (await status_of(system_conn)).stale_edge_documents == 1  # 걸었을 뿐 아직 판정 전
    await drain(worker_conn, FakeProvider())

    assert await wait_for_edge_jobs(system_conn, queued) == 0
    assert (await status_of(system_conn)).stale_edge_documents == 0


# ── 원본 재추출 전량 (openarchive reextract --all) ─────────────────────────


async def upload_original(conn, text: str, *, filename: str = "note.txt"):
    document = await create_document(
        conn, filename=filename, data=text.encode("utf-8"), owner_id="alice"
    )
    return document["id"]


def edit_text(dsn: str, document_id, content: str) -> None:
    """사람이 텍스트를 고친 상태를 만든다. 원본에서 다시 뽑으면 달라진다."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            UPDATE documents SET version = version + 1, content = %s, content_hash = %s
             WHERE id = %s
            """,
            (content, hashlib.sha256(content.encode()).hexdigest(), document_id),
        )


def text_of(dsn: str, document_id) -> tuple[int, str]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT version, content FROM documents WHERE id = %s", (document_id,)
        ).fetchone()


async def test_reextract_all_counts_changed_unchanged_and_failed(system_conn, migrated_db):
    changed = await upload_original(system_conn, "original one")
    edit_text(migrated_db, changed, "edited one")
    unchanged = await upload_original(system_conn, "original two")
    broken = await upload_original(system_conn, "original three")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE document_files SET data = %s WHERE document_id = %s",
            ("한글".encode("cp949"), broken),
        )
    without_original = await insert_test_document(system_conn, title="직접", content="direct")

    summary = await reextract_all(system_conn)

    assert (summary.changed, summary.unchanged) == (1, 1)
    assert summary.failed == [(broken, "텍스트 파일은 UTF-8 인코딩이어야 합니다.")]
    assert text_of(migrated_db, changed) == (3, "original one")
    assert text_of(migrated_db, unchanged) == (1, "original two")
    assert text_of(migrated_db, without_original) == (1, "direct")


async def test_reextract_all_failure_does_not_roll_back_other_documents(migrated_db):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        first = await upload_original(conn, "first original")
        edit_text(migrated_db, first, "first edited")
        blank = await upload_original(conn, "blank original")
        with psycopg.connect(migrated_db) as setup:
            setup.execute(
                "UPDATE document_files SET data = %s WHERE document_id = %s",
                (b" \t\r\n\f", blank),
            )

        summary = await reextract_all(conn)

    assert summary.changed == 1
    assert [document_id for document_id, _ in summary.failed] == [blank]
    # 별도 연결에서 보인다 — 실패한 문서가 앞서 바뀐 문서를 되돌리지 않았다.
    assert text_of(migrated_db, first) == (3, "first original")


async def test_reextract_all_skips_documents_changed_concurrently(system_conn, migrated_db):
    first = await upload_original(system_conn, "first original")
    edit_text(migrated_db, first, "first edited")
    second = await upload_original(system_conn, "second original")
    edit_text(migrated_db, second, "second edited")
    progress = []

    def on_progress(done, total):
        progress.append((done, total))
        if done == 1:
            # 대상 조회 뒤, 처리 전에 누군가 두 번째 문서를 편집했다.
            edit_text(migrated_db, second, "second edited again")

    summary = await reextract_all(system_conn, on_progress=on_progress)

    assert summary.changed == 1
    assert [document_id for document_id, _ in summary.failed] == [second]
    assert text_of(migrated_db, first) == (3, "first original")
    assert text_of(migrated_db, second) == (3, "second edited again")
    assert progress == [(1, 2), (2, 2)]


# ── 원본 재추출 한 건 (openarchive reextract <id>) ─────────────────────────


async def test_reextract_one_reports_changed_then_unchanged(system_conn, migrated_db):
    document_id = await upload_original(system_conn, "original")
    edit_text(migrated_db, document_id, "edited")

    first = await reextract_one(system_conn, document_id)
    second = await reextract_one(system_conn, document_id)

    assert (first.changed, first.unchanged, first.failed) == (1, 0, [])
    assert (second.changed, second.unchanged, second.failed) == (0, 1, [])
    assert text_of(migrated_db, document_id) == (3, "original")


async def test_reextract_one_reports_an_extraction_failure_as_failed(
    system_conn, migrated_db
):
    document_id = await upload_original(system_conn, "original")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE document_files SET data = %s WHERE document_id = %s",
            ("한글".encode("cp949"), document_id),
        )

    summary = await reextract_one(system_conn, document_id)

    assert summary.failed == [(document_id, "텍스트 파일은 UTF-8 인코딩이어야 합니다.")]
    assert text_of(migrated_db, document_id) == (1, "original")


async def test_reextract_one_rejects_a_missing_document(system_conn):
    """한 건을 지정한 명령이라 요약의 실패 한 줄이 아니라 거절이다."""
    with pytest.raises(DocumentNotFound):
        await reextract_one(system_conn, "00000000-0000-0000-0000-000000000000")


async def test_reextract_one_rejects_a_document_without_original(system_conn):
    document_id = await insert_test_document(system_conn, title="직접", content="direct")

    with pytest.raises(OriginalFileMissing):
        await reextract_one(system_conn, document_id)


# ── 추출 상태 (ADR-052) ───────────────────────────────────────────────────


async def test_status_counts_documents_waiting_for_and_failing_extraction(
    system_conn, migrated_db
):
    scan = (FIXTURES / "scan_tax_page1.jpg").read_bytes()
    waiting = await create_document(system_conn, filename="a.jpg", data=scan, owner_id="alice")
    failed = await create_document(system_conn, filename="b.jpg", data=scan, owner_id="alice")
    await upload_original(system_conn, "텍스트 문서")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE documents SET extraction_status = 'failed' WHERE id = %s", (failed["id"],)
        )

    result = await status_of(system_conn)

    assert (result.extraction_pending, result.extraction_failed) == (1, 1)
    assert waiting["extraction_status"] == "pending"


def point_original_at_scan(dsn: str, document_id) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE document_files SET filename = 'scan.jpg', data = %s WHERE document_id = %s",
            ((FIXTURES / "scan_tax_page1.jpg").read_bytes(), document_id),
        )


async def test_reextract_one_hands_an_ocr_target_to_the_worker(system_conn, migrated_db):
    document_id = await upload_original(system_conn, "original")
    point_original_at_scan(migrated_db, document_id)

    summary = await reextract_one(system_conn, document_id)

    assert (summary.changed, summary.unchanged, summary.awaiting_ocr, summary.failed) == (
        0,
        0,
        1,
        [],
    )
    assert text_of(migrated_db, document_id) == (1, "original")


async def test_reextract_all_counts_ocr_targets_and_skips_documents_in_extraction(
    system_conn, migrated_db
):
    ocr_target = await upload_original(system_conn, "original")
    point_original_at_scan(migrated_db, ocr_target)
    in_progress = await create_document(
        system_conn,
        filename="scan.jpg",
        data=(FIXTURES / "scan_tax_page1.jpg").read_bytes(),
        owner_id="alice",
    )

    summary = await reextract_all(system_conn)

    assert (summary.changed, summary.unchanged, summary.awaiting_ocr) == (0, 0, 1)
    assert [document_id for document_id, _ in summary.failed] == [in_progress["id"]]


async def test_preview_rebuild_returns_the_pending_plate_count(system_conn, migrated_db):
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        doc = insert_document(conn)
        insert_preview_original(conn, doc, "a.hwp")
        insert_preview_original(conn, doc, "b.pdf", 2)
        conn.execute(
            "UPDATE document_file_previews SET status = 'unavailable', error = 'missing'"
        )

    assert await enqueue_preview_rebuild(system_conn) == 1
    assert await (await system_conn.execute(
        "SELECT status FROM document_file_previews"
    )).fetchall() == [("pending",)]
