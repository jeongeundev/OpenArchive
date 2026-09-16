import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from test_triggers import edges_for, insert_document, mark_document_ready, unit_vector

from app.embeddings import FakeProvider
from app.services.system import get_system_status, rebuild_all_edges


@pytest.fixture
async def system_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(
        migrated_db, autocommit=True
    ) as conn:
        yield conn


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


async def test_rebuild_all_edges_lets_earlier_documents_see_later_ones(system_conn, migrated_db):
    with psycopg.connect(migrated_db, autocommit=True) as setup:
        first = insert_document(setup)
        mark_document_ready(setup, first, ["first"], vectors=[unit_vector(0)])
        second = insert_document(setup)
        mark_document_ready(setup, second, ["second"], vectors=[unit_vector(0)])
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
