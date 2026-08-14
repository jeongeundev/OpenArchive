import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from app.embeddings import FakeProvider
from app.services.system import get_system_status


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
