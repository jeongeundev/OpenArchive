# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from openarchive import db
from openarchive.services.db_outages import (
    OutageTracker,
    list_outages,
    probe,
    record_outage,
    run_monitor,
)


@pytest.mark.parametrize(
    ("failures", "timeline", "kind"),
    [(1, 1, None), (2, 1, "interruption"), (1, 2, "promotion"), (0, 2, "promotion")],
)
def test_tracker(failures, timeline, kind):
    tracker = OutageTracker()
    at = datetime.now(UTC)
    tracker.observe_ok(at, "before:5432", 1, (2, 3, 4))
    for i in range(failures):
        tracker.observe_failure(at + timedelta(seconds=i + 1))
    assert tracker.in_outage == bool(failures)
    result = tracker.observe_ok(at + timedelta(seconds=5), "after:5432", timeline, (5, 7, 9))
    assert not tracker.in_outage
    if kind is None:
        assert result is None
    else:
        assert result.kind == kind
        assert result.failed_checks == failures
        assert result.node_before == "before:5432"
        assert result.node_after == "after:5432"
        assert result.first_failure_at == at + timedelta(seconds=1 if failures else 5)
        assert result.timeline_before == 1
        assert result.timeline_after == timeline
        assert result.last_ok_at == at
        assert result.recovered_at == at + timedelta(seconds=5)
        assert (result.requests_retried, result.requests_unavailable, result.server_errors) == (
            3,
            4,
            5,
        )


async def test_probe_and_records(migrated_db):
    async with await db.connect(migrated_db, autocommit=True) as conn:
        node, timeline = await probe(conn)
        assert node is None or node.endswith(":5432")
        assert isinstance(timeline, int) and timeline > 0
        tracker = OutageTracker()
        at = datetime.now(UTC)
        tracker.observe_ok(at, node, timeline, (0, 0, 0))
        for i in (1, 2):
            tracker.observe_failure(at + timedelta(seconds=i))
        outage = tracker.observe_ok(at + timedelta(seconds=3), node, timeline, (1, 2, 3))
        await record_outage(conn, outage)
        outage.recovered_at += timedelta(seconds=1)
        await record_outage(conn, outage)
        rows = await list_outages(conn)
        assert len(rows) == 2
        assert rows[0]["recovered_at"] > rows[1]["recovered_at"]
        assert rows[0]["requests_unavailable"] == 2
        assert len(await list_outages(conn, limit=1)) == 1


@pytest.mark.parametrize("reachable", [True, False])
async def test_monitor_runs_and_cancels(migrated_db, reachable):
    tracker = OutageTracker()
    dsn = migrated_db if reachable else "postgresql://nobody@127.0.0.1:1/nope"
    task = asyncio.create_task(run_monitor(dsn, interval=0.01, timeout=0.1, tracker=tracker))
    try:
        async with asyncio.timeout(3):
            while not (tracker.last_ok_at if reachable else tracker.failed_checks >= 2):
                await asyncio.sleep(0.01)
        if reachable:
            async with await db.connect(migrated_db, autocommit=True) as conn:
                assert await list_outages(conn) == []
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_monitor_retries_unsaved_record(migrated_db, caplog):
    tracker = OutageTracker()
    async with await db.connect(migrated_db, autocommit=True) as conn:
        node, timeline = await probe(conn)
        at = datetime.now(UTC) - timedelta(seconds=3)
        tracker.observe_ok(at, node, timeline, (0, 0, 0))
        tracker.observe_failure(at + timedelta(seconds=1))
        tracker.observe_failure(at + timedelta(seconds=2))
        await conn.execute("ALTER TABLE db_outages RENAME TO db_outages_held")
        task = asyncio.create_task(
            run_monitor(migrated_db, interval=0.01, timeout=1, tracker=tracker)
        )
        restored = False
        try:
            async with asyncio.timeout(3):
                while "DB 장애 기록 저장 실패" not in caplog.text:
                    await asyncio.sleep(0.01)
            await conn.execute("ALTER TABLE db_outages_held RENAME TO db_outages")
            restored = True
            async with asyncio.timeout(3):
                while not (rows := await list_outages(conn)):
                    await asyncio.sleep(0.01)
            assert len(rows) == 1
            assert rows[0]["failed_checks"] == 2
            assert rows[0]["last_ok_at"] == at
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            if not restored:
                await conn.execute("ALTER TABLE db_outages_held RENAME TO db_outages")
