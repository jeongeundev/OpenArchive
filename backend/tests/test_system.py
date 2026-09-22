import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from test_triggers import edges_for, insert_document, mark_document_ready, unit_vector

from app.embeddings import FakeProvider
from app.services.system import get_system_status, rebuild_all_edges
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
    # 임베딩 전에는 관계 잡 자체가 없다 — ready 전이가 만든다 (016).
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
        # 016 이후 ready 전이는 관계 잡만 만든다 — 워커가 하는 판정을 여기서 대신 돌려
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
