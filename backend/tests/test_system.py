import hashlib

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from test_triggers import edges_for, insert_document, mark_document_ready, unit_vector

from app.embeddings import FakeProvider
from app.services.documents import DocumentNotFound, OriginalFileMissing, create_document
from app.services.system import (
    get_system_status,
    rebuild_all_edges,
    reextract_all,
    reextract_one,
)
from app.worker import process_once


@pytest.fixture
async def system_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(
        migrated_db, autocommit=True
    ) as conn:
        yield conn


async def status_of(conn, *, zombie_timeout_minutes: int = 5):
    return await get_system_status(
        conn,
        zombie_timeout_minutes=zombie_timeout_minutes,
        embedding_provider="fake",
    )


async def test_empty_database_has_no_jobs_or_finished_job(system_conn):
    result = await get_system_status(
        system_conn,
        zombie_timeout_minutes=5,
        embedding_provider="fake",
    )

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
        zombie_timeout_minutes=5,
        embedding_provider="fake",
    )
    assert pending.jobs.pending == 1

    await process_all_embedding_jobs(system_conn, FakeProvider())

    completed = await get_system_status(
        system_conn,
        zombie_timeout_minutes=5,
        embedding_provider="fake",
    )
    assert completed.jobs.pending == 0
    assert completed.last_job_finished_at is not None


async def test_status_includes_values_supplied_by_the_caller(system_conn):
    result = await get_system_status(
        system_conn,
        zombie_timeout_minutes=17,
        embedding_provider="test-provider",
    )

    assert result.zombie_timeout_minutes == 17
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
           SET status = 'processing', started_at = now() - interval '10 minutes'
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


async def test_rebuild_all_edges_lets_earlier_documents_see_later_ones(system_conn, migrated_db):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        first = insert_document(setup)
        mark_document_ready(setup, first, ["first"], vectors=[unit_vector(0)])
        second = insert_document(setup)
        mark_document_ready(setup, second, ["second"], vectors=[unit_vector(0)])
        # 017 이후 ready 전이는 관계 잡만 만든다 — 워커가 하는 판정을 여기서 대신 돌려
        # "나중 문서만 자기 관계를 계산한 상태"를 그대로 재현한다.
        setup.execute("SELECT rebuild_document_edges(%s)", (second,))
        assert [(row[0], row[1]) for row in edges_for(setup, first)] == [(second, first)]

        assert await rebuild_all_edges(system_conn) == 2
        rebuilt = edges_for(setup, first)
        assert {(row[0], row[1]) for row in rebuilt} == {(first, second), (second, first)}
        assert await rebuild_all_edges(system_conn) == 2
        assert edges_for(setup, first) == rebuilt


async def test_rebuild_all_edges_skips_documents_that_are_not_ready(system_conn, migrated_db):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        ready = insert_document(setup)
        mark_document_ready(setup, ready, ["ready"], vectors=[unit_vector(0)])
        pending = insert_document(setup)

        assert await rebuild_all_edges(system_conn) == 1
        assert setup.execute(
            "SELECT count(*) FROM document_edges WHERE src_document_id = %s", (pending,)
        ).fetchone() == (0,)
        assert setup.execute(
            "SELECT embedding_status FROM documents WHERE id = %s", (pending,)
        ).fetchone() == ("pending",)


async def test_rebuild_all_edges_closes_the_edge_jobs_whose_work_it_did(system_conn):
    """전량 재계산은 **격리된** 관계 잡을 마감한다 — 안 그러면 카운터가 영구히 >0이다.

    관계 잡이 재시도 예산을 소진해 error로 격리되면 그 문서는 관계 미반영 수에 계속
    세어진다(의도된 동작이다 — 격리했다고 어긋남을 숨기지 않는다). 그 상태의 복구
    경로로 OPERATIONS가 안내하는 것이 `openarchive rebuild-edges`인데, 재계산이 잡을
    그대로 두면 관계를 실제로 복구하고도 지표는 영영 내려오지 않는다.
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

    assert await rebuild_all_edges(system_conn) == 1

    assert (await status_of(system_conn)).stale_edge_documents == 0


async def test_rebuild_all_edges_leaves_the_jobs_the_worker_still_owns(system_conn):
    """대기·처리 중인 관계 잡은 건드리지 않는다 — 그것은 워커가 가진 잡이다.

    함께 마감하면 재계산이 청크를 읽은 뒤에 커밋된 ready 전이의 잡까지 지워, 마지막
    청크 교체가 반영되지 않은 관계를 가진 채 카운터만 0이 되는 자리가 생긴다.
    """
    await insert_test_document(
        system_conn,
        title="워커의 잡",
        content="관계 잡이 아직 대기 중인 문서.",
    )
    assert await process_once(system_conn, FakeProvider())  # 임베딩 잡 → ready + 관계 잡

    assert await rebuild_all_edges(system_conn) == 1

    assert (await status_of(system_conn)).stale_edge_documents == 1


@pytest.mark.parametrize("autocommit", [False, True])
async def test_rebuild_all_edges_commits_per_document(migrated_db, autocommit):
    with psycopg.connect(migrated_db, autocommit=True) as observer:
        for _ in range(3):
            doc_id = insert_document(observer)
            mark_document_ready(observer, doc_id, ["text"], vectors=[unit_vector(0)])
        ordered_ids = [row[0] for row in observer.execute(
            "SELECT id FROM documents ORDER BY created_at, id"
        ).fetchall()]
        progress = []

        def on_progress(done, total):
            progress.append((done, total))
            # 별도 연결은 커밋된 결과만 본다. 첫 문서는 원래 나가는 관계가 없다.
            assert observer.execute(
                "SELECT count(*) FROM document_edges WHERE src_document_id = %s",
                (ordered_ids[done - 1],),
            ).fetchone()[0] == 2

        async with await psycopg.AsyncConnection.connect(
            migrated_db, autocommit=autocommit
        ) as conn:
            assert await rebuild_all_edges(conn, on_progress=on_progress) == 3
        assert progress == [(1, 3), (2, 3), (3, 3)]


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
