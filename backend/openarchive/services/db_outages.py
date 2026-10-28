# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""API가 관측한 연결 중단만 기록한다. API가 꺼진 동안의 장애는 알 수 없다."""

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from psycopg.rows import dict_row

from openarchive import db
from openarchive.api.retry import request_counters

logger = logging.getLogger(__name__)


@dataclass
class Outage:
    kind: str
    last_ok_at: datetime
    first_failure_at: datetime
    recovered_at: datetime
    node_before: str | None
    node_after: str | None
    timeline_before: int | None
    timeline_after: int | None
    failed_checks: int
    requests_retried: int
    requests_unavailable: int
    server_errors: int


class OutageTracker:
    def __init__(self):
        self.last_ok_at = None
        self.node = None
        self.timeline = None
        self.counters = (0, 0, 0)
        self.first_failure_at = None
        self.failed_checks = 0

    @property
    def in_outage(self):
        return self.failed_checks > 0

    def observe_failure(self, at):
        if not self.failed_checks:
            self.first_failure_at = at
        self.failed_checks += 1

    def observe_ok(self, at, node, timeline, counters):
        outage = None
        if self.last_ok_at is not None and (self.failed_checks >= 2 or timeline != self.timeline):
            outage = Outage(
                "promotion" if timeline != self.timeline else "interruption",
                self.last_ok_at,
                self.first_failure_at or at,
                at,
                self.node,
                node,
                self.timeline,
                timeline,
                self.failed_checks,
                *(a - b for a, b in zip(counters, self.counters)),
            )
        self.last_ok_at = at
        self.node, self.timeline, self.counters = node, timeline, tuple(counters)
        self.first_failure_at = None
        self.failed_checks = 0
        return outage


async def probe(conn) -> tuple[str | None, int]:
    async with conn.transaction():
        row = await (
            await conn.execute(
                "SELECT host(inet_server_addr()), inet_server_port(), "
                "('x' || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8))::bit(32)::int"
            )
        ).fetchone()
    address, port, timeline = row
    return (f"{address}:{port}" if address is not None else None), timeline


async def record_outage(conn, outage: Outage):
    values = asdict(outage)
    columns = ", ".join(values)
    parameters = ", ".join(f"%({key})s" for key in values)
    async with conn.transaction():
        await conn.execute(f"INSERT INTO db_outages ({columns}) VALUES ({parameters})", values)


async def list_outages(conn, *, limit=20) -> list[dict]:
    async with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT id, kind, last_ok_at, first_failure_at, recovered_at, "
            "node_before, node_after, timeline_before, timeline_after, failed_checks, "
            "requests_retried, requests_unavailable, server_errors FROM db_outages "
            "ORDER BY recovered_at DESC, id DESC LIMIT %s",
            (limit,),
        )
        return await cursor.fetchall()


async def run_monitor(dsn, *, interval=2.0, timeout=3.0, tracker=None):
    tracker = tracker if tracker is not None else OutageTracker()
    conn = None
    pending = []

    async def check():
        nonlocal conn
        if conn is None:
            conn = await db.connect(dsn, autocommit=True)
        return await probe(conn)

    try:
        while True:
            at = datetime.now(UTC)
            try:
                node, timeline = await asyncio.wait_for(check(), timeout)
            except Exception:
                tracker.observe_failure(at)
                if tracker.failed_checks == 1:
                    logger.warning("DB 감시 탐침 실패", exc_info=True)
                if conn is not None:
                    await conn.close()
                    conn = None
            else:
                outage = tracker.observe_ok(
                    datetime.now(UTC), node, timeline, request_counters.snapshot()
                )
                if outage is not None:
                    pending.append(outage)
                    logger.warning("DB 연결 복구: %s", outage)
                while pending:
                    try:
                        await asyncio.wait_for(record_outage(conn, pending[0]), timeout)
                    except Exception:
                        logger.warning(
                            "DB 장애 기록 저장 실패 — 다음 정상 탐침에서 재시도", exc_info=True
                        )
                        await conn.close()
                        conn = None
                        break
                    else:
                        logger.warning("DB 장애 기록 저장: %s", pending.pop(0))
            await asyncio.sleep(interval)
    finally:
        if conn is not None:
            await conn.close()
