"""HA 장애 주입 측정 + 자동 정합성 검사 (#122, ADR-047~050).

앱(API·워커)을 VIP에 붙여 띄운 채로 부하를 걸고, 정해진 시각에 장애 명령을 실행한 뒤
카운터가 0으로 수렴할 때까지 기다려 판정한다. 판정 항목은 다섯 가지다.

- 장부 대조: 커밋 응답(2xx)을 받은 업로드가 전부 DB에 있고 내용 sha256이 같은가(유실 0),
  실패 응답인데 DB에 남은 행·같은 제목의 중복 행이 없는가(멱등키, ADR-047)
- 사용자 가시 실패: 웹 UI·MCP와 같은 백오프(1초 시작·상한 8초·전체 지터·60초)를 거친 뒤의
  최종 실패 수. 원시 응답의 500(코드 결함 신호, ADR-048)은 따로 세며 0이어야 한다.
  웹 UI와 달리 요청마다 15초 타임아웃을 두고 타임아웃도 다시 보낸다 — 매달린 요청 하나가
  측정을 멈추지 않게 하려는 것이다. 503을 받은 요청의 수·구간은 판정 밖에서 따로 남긴다
- 정합성 카운터(대기·처리 중 잡, 청크 버전 불일치, 관계 미반영, 미준비 문서) 0 수렴
- 노드 대조: 노드마다 5432로 직접 붙어 이 회차 문서의 수·내용 다이제스트(md5)가 같은가
- 승격 대상: 리더가 바뀌었다면 새 리더가 주입 전 동기 standby였는가(ADR-049). 첫 상태와
  마지막 상태만 비교하므로 한 회차에 리더가 두 번 바뀌면 중간 승격은 판정하지 못한다

쓰기 중단 시간(RTO)은 100ms마다 VIP에 새 연결로 WAL을 남기는 트랜잭션을 커밋하는 probe로 잰다.
`txid_current()`만으로는 커밋이 WAL을 쓰지 않아 동기 복제 대기를 타지 않는다 — 동기 standby가 죽어
커밋이 멈춘 구간을 놓친다(S5a). probe의 실패는 관측값이며 판정에 넣지 않는다.

노드 대조는 5432에 직접 붙는다. VIP 단일 엔드포인트 규칙(ADR-006)은 애플리케이션의 규칙이고,
이 도구의 목적이 VIP 뒤의 노드끼리 같은지 보는 것이다. 부하·probe·상태 조회는 VIP로 붙는다.

사용 (저장소 루트에서, API·워커가 --api와 같은 DB로 떠 있어야 한다):
  DATABASE_URL=postgresql://…@<VIP>:6432/opensql \\
  backend/.venv/bin/python scripts/ha_failover.py run s2-1 --nodes 192.168.64.201,192.168.64.202,192.168.64.203 \\
      --inject 'utmctl stop node1; sleep 90; utmctl start node1' --inject-at 40 --load 240
  backend/.venv/bin/python scripts/ha_failover.py report s2-1 --nodes …   # 기록으로 다시 판정

결과는 `--out`(기본 ha-runs/)의 `<label>.jsonl`에 남는다. 종료 코드는 판정 통과 0, 위반 1.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from openarchive.db import keepalive_kwargs

# 클라이언트 백오프 — 웹 UI(`frontend/src/lib/api.ts`)·MCP(`openarchive/mcp_server/server.py`)와 같은 값.
BACKOFF_START_SECONDS = 1
BACKOFF_CAP_SECONDS = 8
BACKOFF_BUDGET_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 15

WORDS = [
    "문서",
    "관리",
    "검색",
    "벡터",
    "임베딩",
    "복제",
    "승격",
    "장애",
    "정합성",
    "버전",
    "청크",
    "관계",
    "태그",
    "권한",
    "트랜잭션",
    "프록시",
]
QUERIES = [
    "복제 지연과 승격",
    "문서 버전 정합성",
    "벡터 검색 권한",
    "태그 관계 청크",
    "연차 휴가 신청 절차",
]
COUNTERS = ("pending", "processing", "inconsistent", "stale_edges", "not_ready")
# 지금 WAL 파일 이름의 앞 8자리 = timeline. 승격마다 1씩 오른다.
TIMELINE_SQL = (
    "('x' || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8))::bit(32)::int"
)


# --- 백오프 ---------------------------------------------------------------------------


def next_delay(attempt: int, retry_after: float | None, *, rand: float) -> float:
    """전체 지터 지수 백오프. `Retry-After`는 대체가 아니라 하한이다."""
    backoff = rand * min(BACKOFF_CAP_SECONDS, BACKOFF_START_SECONDS * 2**attempt)
    return max(retry_after or 0, backoff)


@dataclass
class Outcome:
    final_status: int | None
    error: str | None
    statuses: list[int | None] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.final_status is not None and 200 <= self.final_status < 300


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    return float(value) if value.isdigit() else None


async def call_with_backoff(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> Outcome:
    """503과 전송 오류만 다시 보낸다. 다른 응답(500 포함)은 그대로 최종 결과다."""
    deadline = now() + BACKOFF_BUDGET_SECONDS
    statuses: list[int | None] = []
    attempt = 0
    while True:
        try:
            response = await send()
        except httpx.TransportError as error:
            statuses.append(None)
            last, retry_after = error, None
        else:
            statuses.append(response.status_code)
            if response.status_code != 503:
                return Outcome(response.status_code, None, statuses)
            last, retry_after = response, _retry_after(response)
        delay = next_delay(attempt, retry_after, rand=rand())
        if now() + delay > deadline:
            if isinstance(last, httpx.Response):
                return Outcome(last.status_code, None, statuses)
            return Outcome(None, _describe(last), statuses)
        await sleep(delay)
        attempt += 1


def _describe(error: BaseException) -> str:
    text = str(error).strip()
    return f"{type(error).__name__}: {text.splitlines()[0][:200] if text else ''}"


# --- 판정 ----------------------------------------------------------------------------


def reconcile(ledger: list[dict], rows: list[tuple[str, str, str]]) -> dict:
    """업로드 장부와 DB 행(제목, 저장 해시, 본문 재계산 해시)을 대조한다."""
    by_title: dict[str, list[tuple[str, str]]] = {}
    for title, stored, recomputed in rows:
        by_title.setdefault(title, []).append((stored, recomputed))
    acked = [e for e in ledger if e["ok"]]
    ledger_titles = {e["title"] for e in ledger}
    return {
        "acked": len(acked),
        "rows": len(rows),
        "lost": [e["title"] for e in acked if e["title"] not in by_title],
        "mismatched": [
            e["title"]
            for e in acked
            if e["title"] in by_title
            and any(h != e["sha256"] for pair in by_title[e["title"]] for h in pair)
        ],
        "ghosts": [
            e["title"] for e in ledger if not e["ok"] and e["title"] in by_title
        ],
        "duplicates": sorted(t for t, found in by_title.items() if len(found) > 1),
        "unknown": sorted(set(by_title) - ledger_titles),
    }


def _role_holder(cluster: dict, role: str) -> str | None:
    return next(
        (m["name"] for m in cluster.get("members", []) if m.get("role") == role), None
    )


def promotion(before: dict, after: dict) -> dict:
    """리더가 바뀌었을 때 새 리더가 주입 전 동기 standby였는지. 바뀌지 않았거나 동기 standby가
    없던 클러스터면 판정 대상이 아니다(None)."""
    leader_before = _role_holder(before, "leader")
    sync_before = _role_holder(before, "sync_standby")
    leader_after = _role_holder(after, "leader")
    judged = leader_after != leader_before and sync_before is not None
    return {
        "leader_before": leader_before,
        "sync_before": sync_before,
        "leader_after": leader_after,
        "promoted_sync": (leader_after == sync_before) if judged else None,
    }


def judge(summary: dict) -> list[str]:
    """위반 목록. 비어 있으면 통과다."""
    r = summary["reconcile"]
    problems = []
    if r["lost"]:
        problems.append(
            f"유실 {len(r['lost'])}건 — 커밋 응답을 받은 업로드가 DB에 없다: {r['lost'][:5]}"
        )
    if r["mismatched"]:
        problems.append(f"내용 불일치 {len(r['mismatched'])}건: {r['mismatched'][:5]}")
    if r["ghosts"]:
        problems.append(
            f"실패 응답인데 DB에 있음 {len(r['ghosts'])}건: {r['ghosts'][:5]}"
        )
    if r["duplicates"]:
        problems.append(f"중복 생성 {len(r['duplicates'])}건: {r['duplicates'][:5]}")
    raw_500 = {k: n for k, n in summary["raw_500"].items() if n}
    if raw_500:
        problems.append(f"원시 응답 500 {raw_500} — 코드 결함 신호(ADR-048)")
    final = {k: n for k, n in summary["final_failures"].items() if n}
    if final:
        problems.append(f"백오프 뒤에도 실패한 사용자 요청 {final}")
    if not summary["converged"]:
        problems.append("정합성 카운터가 0으로 수렴하지 않았다")
    if summary["error_jobs"]:
        problems.append(f"error 잡 {summary['error_jobs']}건")
    nodes = summary["nodes"]
    missing = [n for n, digest in nodes.items() if digest is None]
    if missing:
        problems.append(f"노드 대조 불가(접속 실패·미합류): {missing}")
    digests = {digest for digest in nodes.values() if digest is not None}
    if len(digests) > 1:
        problems.append(f"노드 간 md5 불일치: {nodes}")
    if summary["promotion"]["promoted_sync"] is False:
        problems.append(f"동기 standby가 아닌 노드가 승격됐다: {summary['promotion']}")
    return problems


def write_outages(
    probes: list[dict], *, since: float
) -> list[tuple[float, float | None]]:
    """쓰기 중단 구간들: 주입 뒤 실패마다, 직전의 마지막 성공 ~ 직후의 첫 성공.

    사이에 성공이 하나라도 있으면 구간을 나눈다 — 전원 차단과 VIP 선점 복귀처럼 떨어진
    실패를 하나로 합치면 그 사이 정상 구간까지 중단으로 잡힌다(#151).
    이벤트는 끝난 시각으로 찍힌다. 주입 순간 매달린 probe는 타임아웃 뒤에야 실패로 남으므로
    첫 실패 시각으로 재면 중단이 그만큼 짧게 잡힌다.
    """
    outages: list[tuple[float, float | None]] = []
    last_ok: float | None = None
    start: float | None = None
    for e in probes:
        if e["ok"]:
            if start is not None:
                outages.append((start, e["t"]))
                start = None
            last_ok = e["t"]
        elif e["t"] >= since and start is None:
            start = last_ok if last_ok is not None else e["t"]
    if start is not None:
        outages.append((start, None))
    return outages


def tally(events: list[dict]) -> dict:
    """기록에서 DB 없이 셀 수 있는 판정 입력. 장부 대조·노드 대조는 summarize가 붙인다."""
    by = lambda kind: [e for e in events if e["kind"] == kind]
    clusters = by("cluster")
    return {
        "raw_500": {
            k: sum(s == 500 for e in by(k) for s in e["statuses"])
            for k in ("upload", "search")
        },
        "final_failures": {
            k: sum(not e["ok"] for e in by(k)) for k in ("upload", "search")
        },
        "converged": next(
            (e["converged"] for e in by("meta") if e.get("phase") == "end"), False
        ),
        # error는 끝 상태다 — 한 번이라도 보였으면 위반이다
        "error_jobs": max(
            (e["error_jobs"] for e in by("status") if e["ok"]), default=0
        ),
        "promotion": promotion(clusters[0], clusters[-1])
        if clusters
        else {"promoted_sync": None},
    }


def unavailable_spans(
    requests: list[dict], *, gap: float = 2.0
) -> list[tuple[float, float, int]]:
    """503을 한 번이라도 받은 요청의 구간(시작 = 끝 시각 - 지연)을 합친다. (시작, 끝, 요청 수)."""
    spans: list[tuple[float, float, int]] = []
    for start, end in sorted(
        (e["t"] - e["lat"], e["t"]) for e in requests if 503 in e["statuses"]
    ):
        if spans and start <= spans[-1][1] + gap:
            s, e, n = spans[-1]
            spans[-1] = (s, max(e, end), n + 1)
        else:
            spans.append((start, end, 1))
    return spans


# --- DB 대조 쿼리 ----------------------------------------------------------------------


def _like_prefix(prefix: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", prefix) + "%"


def ledger_rows(conn: psycopg.Connection, prefix: str) -> list[tuple[str, str, str]]:
    return conn.execute(
        "SELECT title, content_hash, encode(sha256(convert_to(content, 'UTF8')), 'hex')"
        " FROM documents WHERE title LIKE %s",
        (_like_prefix(prefix),),
    ).fetchall()


def node_digest(conn: psycopg.Connection, prefix: str) -> str:
    """이 회차 문서의 수·청크 수·내용을 한 값으로. 노드마다 같아야 한다."""
    return conn.execute(
        """
        SELECT count(*) || ':' || (SELECT count(*) FROM document_chunks c
                                   JOIN documents d2 ON d2.id = c.document_id
                                   WHERE d2.title LIKE %s)
               || ':' || coalesce(md5(string_agg(
                    id::text || content_hash || md5(content) || version, ',' ORDER BY id)), '')
        FROM documents WHERE title LIKE %s
        """,
        (_like_prefix(prefix), _like_prefix(prefix)),
    ).fetchone()[0]


STATUS_SQL = f"""
SELECT host(inet_server_addr()) AS server,
       {TIMELINE_SQL} AS tl,
       (SELECT count(*) FROM embedding_jobs WHERE kind = 'embed' AND status = 'pending') AS pending,
       (SELECT count(*) FROM embedding_jobs WHERE kind = 'embed' AND status = 'processing')
         AS processing,
       (SELECT count(*) FROM embedding_jobs j JOIN documents d ON d.id = j.document_id
         WHERE j.status = 'error' AND d.title LIKE %(like)s) AS error_jobs,
       (SELECT count(DISTINCT c.document_id) FROM document_chunks c
          JOIN documents d ON d.id = c.document_id WHERE c.version <> d.version) AS inconsistent,
       (SELECT count(DISTINCT document_id) FROM embedding_jobs WHERE kind = 'edges' AND status <> 'done'
                                                                   AND status <> 'error')
         AS stale_edges,
       (SELECT count(*) FROM documents WHERE title LIKE %(like)s AND embedding_status <> 'ready')
         AS not_ready,
       (SELECT max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn))::bigint FROM pg_stat_replication)
         AS lag,
       (SELECT string_agg(host(client_addr) || '=' || sync_state, ',' ORDER BY client_addr)
          FROM pg_stat_replication) AS replicas
"""


# --- 실행 ----------------------------------------------------------------------------


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = path.open("a", buffering=1)

    def __call__(self, kind: str, **kw) -> None:
        kw.update(t=time.time(), kind=kind)
        self.f.write(json.dumps(kw, ensure_ascii=False) + "\n")


@dataclass
class Target:
    dsn: str
    api: str
    nodes: list[str]
    prefix: str

    def conn_kwargs(self) -> dict:
        # 측정기도 VIP에 붙는 이상 반쯤 열린 연결(#110 B-1)에 걸린다 — 앱과 같은 keepalive.
        return keepalive_kwargs(self.dsn) | {"connect_timeout": 2}

    def node_dsn(self, host: str) -> str:
        return make_conninfo(self.dsn, host=host, port="5432")


async def probe(log: Log, stop: asyncio.Event, target: Target) -> None:
    while not stop.is_set():
        t = time.time()
        try:
            async with asyncio.timeout(5):
                async with await psycopg.AsyncConnection.connect(
                    target.dsn, **target.conn_kwargs()
                ) as conn:
                    row = await (
                        await conn.execute(
                            "SELECT host(inet_server_addr()), pg_logical_emit_message(true, 'ha-probe', ''),"
                            f" {TIMELINE_SQL}"
                        )
                    ).fetchone()
            log("probe", ok=True, lat=time.time() - t, server=row[0], tl=row[2])
        except Exception as error:  # noqa: BLE001 — 실패 자체가 관측값
            log("probe", ok=False, lat=time.time() - t, error=_describe(error))
        await asyncio.sleep(max(0, 0.1 - (time.time() - t)))


def _log_outcome(log: Log, kind: str, t: float, out: Outcome, **kw) -> None:
    log(
        kind,
        ok=out.ok,
        lat=time.time() - t,
        final_status=out.final_status,
        statuses=out.statuses,
        error=out.error,
        **kw,
    )


async def uploader(
    log: Log, stop: asyncio.Event, client: httpx.AsyncClient, target: Target
) -> None:
    seq = 0
    while not stop.is_set():
        seq += 1
        title = f"{target.prefix}{seq:05d}"
        body = (
            " ".join(random.choices(WORDS, k=60))
            + f"\n\n고유 표식 {title} {random.getrandbits(64):x}."
        )
        content = f"# {title}\n\n{body}"
        headers = {
            "Idempotency-Key": str(uuid.uuid4())
        }  # 재시도마다 같은 키 — 한 번만 생긴다
        t = time.time()
        out = await call_with_backoff(
            functools.partial(
                client.post,
                f"{target.api}/documents/text",
                headers=headers,
                json={"title": title, "content": content, "content_type": "md"},
            )
        )
        _log_outcome(
            log,
            "upload",
            t,
            out,
            title=title,
            sha256=hashlib.sha256(content.encode()).hexdigest(),
        )
        await asyncio.sleep(max(0, 0.5 - (time.time() - t)))


async def searcher(
    log: Log, stop: asyncio.Event, client: httpx.AsyncClient, target: Target
) -> None:
    while not stop.is_set():
        t = time.time()
        out = await call_with_backoff(
            functools.partial(
                client.post,
                f"{target.api}/search",
                json={"query": random.choice(QUERIES), "k": 5},
            )
        )
        _log_outcome(log, "search", t, out)
        await asyncio.sleep(max(0, 0.33 - (time.time() - t)))


async def blob_writer(log: Log, stop: asyncio.Event, target: Target) -> None:
    """무거운 부하: 20MB bytea를 넣고 비우기를 반복해 복제 지연을 키운다."""
    blob = os.urandom(20_000_000)
    while not stop.is_set():
        t = time.time()
        try:
            async with await psycopg.AsyncConnection.connect(
                target.dsn, **target.conn_kwargs()
            ) as conn:
                async with conn.transaction():
                    await conn.execute("CREATE TABLE IF NOT EXISTS _ha_blob(b bytea)")
                while not stop.is_set():
                    t = time.time()
                    async with conn.transaction():
                        await conn.execute("INSERT INTO _ha_blob VALUES (%s)", (blob,))
                    async with conn.transaction():
                        await conn.execute("TRUNCATE _ha_blob")
                    log("blob", ok=True, lat=time.time() - t)
        except Exception as error:  # noqa: BLE001
            log("blob", ok=False, lat=time.time() - t, error=_describe(error))
            await asyncio.sleep(0.5)


async def status_once(target: Target) -> dict:
    # 트랜잭션 안(autocommit 아님)이라 OpenProxy가 Primary로 보낸다 — 방금 쓴 것을 본다.
    async with asyncio.timeout(5):
        async with await psycopg.AsyncConnection.connect(
            target.dsn, row_factory=dict_row, **target.conn_kwargs()
        ) as conn:
            return await (
                await conn.execute(STATUS_SQL, {"like": _like_prefix(target.prefix)})
            ).fetchone()


async def status_loop(log: Log, stop: asyncio.Event, target: Target) -> None:
    while not stop.is_set():
        t = time.time()
        try:
            log("status", ok=True, **await status_once(target))
        except Exception as error:  # noqa: BLE001
            log("status", ok=False, error=_describe(error))
        await asyncio.sleep(max(0, 1.0 - (time.time() - t)))


async def cluster_state(client: httpx.AsyncClient, nodes: list[str]) -> dict | None:
    """Patroni REST `/cluster`. 죽은 노드를 건너뛰며 처음 답한 노드의 관점을 쓴다."""
    for host in nodes:
        try:
            r = await client.get(f"http://{host}:8008/cluster", timeout=2)
            data = r.json()
            return {
                "members": [
                    {k: m.get(k) for k in ("name", "role", "state", "timeline")}
                    for m in data.get("members", [])
                ]
            }
        except Exception:  # noqa: BLE001, S112 — 다음 노드에 묻는다
            continue
    return None


async def cluster_loop(
    log: Log, stop: asyncio.Event, client: httpx.AsyncClient, nodes: list[str]
) -> None:
    last = None
    while not stop.is_set():
        state = await cluster_state(client, nodes)
        if state is not None and state != last:
            log("cluster", **state)
            last = state
        await asyncio.sleep(1)


async def _arp(host: str) -> str | None:
    p = await asyncio.create_subprocess_exec(
        "arp",
        "-n",
        host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await p.communicate()
    m = re.search(r" at ([0-9a-f:]+)", out.decode())
    return m.group(1) if m else None


async def vip_loop(log: Log, stop: asyncio.Event, target: Target) -> None:
    """VIP를 가진 노드를 ARP 테이블로 추적한다(변화 시에만 기록). 노드 MAC은 시작 때 배운다."""
    vip = conninfo_to_dict(target.dsn)["host"]
    macs = {}
    for host in target.nodes:
        await (
            await asyncio.create_subprocess_exec(
                "ping", "-c1", "-t1", host, stdout=asyncio.subprocess.DEVNULL
            )
        ).wait()
        if mac := await _arp(host):
            macs[mac] = host
    last = None
    while not stop.is_set():
        rc = await (
            await asyncio.create_subprocess_exec(
                "ping", "-c1", "-t1", vip, stdout=asyncio.subprocess.DEVNULL
            )
        ).wait()
        mac = await _arp(vip)
        cur = (macs.get(mac, mac), rc == 0)
        if cur != last:
            log("vip", holder=cur[0], ping=cur[1])
            last = cur
        await asyncio.sleep(0.5)


async def inject(log: Log, cmd: str, at: float) -> None:
    await asyncio.sleep(at)
    log("inject", phase="start", cmd=cmd)
    p = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await p.communicate()
    log("inject", phase="done", rc=p.returncode, out=out.decode()[-500:])


def digests(target: Target, wait: float) -> dict[str, str | None]:
    """노드마다 5432로 직접 붙어 다이제스트를 잰다. 재생이 따라잡을 때까지 `wait`초 동안 다시 잰다."""
    deadline = time.time() + wait
    while True:
        found: dict[str, str | None] = {}
        for host in target.nodes:
            try:
                with psycopg.connect(
                    target.node_dsn(host), **target.conn_kwargs()
                ) as conn:
                    found[host] = node_digest(conn, target.prefix)
            except Exception:  # noqa: BLE001 — 접속 실패는 None으로 판정에 넘긴다
                found[host] = None
        values = set(found.values())
        if (None not in values and len(values) == 1) or time.time() >= deadline:
            return found
        time.sleep(3)


async def run(a: argparse.Namespace, target: Target, path: Path) -> None:
    if path.exists():
        sys.exit(f"{path}가 이미 있다 — 다른 --label을 쓴다")
    log = Log(path)
    log(
        "meta",
        phase="begin",
        label=a.label,
        prefix=target.prefix,
        inject=a.inject,
        inject_at=a.inject_at,
        load=a.load,
        nodes=target.nodes,
    )
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        (
            await client.post(
                f"{target.api}/auth/login",
                json={"username": a.user, "password": a.password},
            )
        ).raise_for_status()
        stop_load, stop_watch = asyncio.Event(), asyncio.Event()
        tasks = [
            asyncio.create_task(probe(log, stop_load, target)),
            asyncio.create_task(uploader(log, stop_load, client, target)),
            asyncio.create_task(searcher(log, stop_load, client, target)),
            asyncio.create_task(status_loop(log, stop_watch, target)),
            asyncio.create_task(cluster_loop(log, stop_watch, client, target.nodes)),
            asyncio.create_task(vip_loop(log, stop_watch, target)),
        ]
        if a.blob:
            tasks.append(asyncio.create_task(blob_writer(log, stop_load, target)))
        if a.inject:
            tasks.append(asyncio.create_task(inject(log, a.inject, a.inject_at)))
        print(
            f"[{a.label}] 부하 {a.load:.0f}초"
            + (f" · +{a.inject_at:.0f}초에 주입: {a.inject}" if a.inject else "")
        )
        await asyncio.sleep(a.load)
        stop_load.set()
        log("meta", phase="load_stopped")
        print(
            f"[{a.label}] 부하 종료 — 카운터 0 수렴·노드 합류 대기(상한 {a.settle:.0f}초)"
        )

        deadline, streak = time.time() + a.settle, 0
        while time.time() < deadline and streak < 5:
            try:
                counters = await status_once(target)
                zero = all(counters[k] == 0 for k in COUNTERS)
                members = (await cluster_state(client, target.nodes) or {}).get(
                    "members", []
                )
                joined = len(members) == len(target.nodes) and all(
                    m["state"] in ("running", "streaming") for m in members
                )
                streak = streak + 1 if zero and joined else 0
            except Exception:  # noqa: BLE001
                streak = 0
            await asyncio.sleep(1)
        stop_watch.set()
        await asyncio.gather(*tasks)
        log("meta", phase="end", converged=streak >= 5)


def _pct(sorted_lat: list[float], q: float) -> float:
    return (
        sorted_lat[min(len(sorted_lat) - 1, int(q * len(sorted_lat)))] * 1000
        if sorted_lat
        else float("nan")
    )


def summarize(path: Path, target: Target) -> tuple[dict, list[str]]:
    ev = [json.loads(line) for line in path.open()]
    by = lambda kind: [e for e in ev if e["kind"] == kind]
    meta = by("meta")
    injected = [e for e in by("inject") if e["phase"] == "start"]
    t0 = injected[0]["t"] if injected else meta[0]["t"]
    lines = [f"== {meta[0]['label']}  (t0 = {'주입' if injected else '시작'})"]

    for kind in ("probe", "upload", "search", "blob"):
        es = by(kind)
        if not es:
            continue
        fails = [e for e in es if not e["ok"]]
        lat = sorted(e["lat"] for e in es if e["ok"])
        line = (
            f"-- {kind}: {len(es)}건 · 최종 실패 {len(fails)} · "
            f"지연 p50 {_pct(lat, 0.5):.0f}ms p99 {_pct(lat, 0.99):.0f}ms"
        )
        if kind in ("upload", "search"):
            raw = Counter(s for e in es for s in e["statuses"])
            retried = sum(1 for e in es if len(e["statuses"]) > 1)
            line += f" · 원시 응답 {dict(raw)} · 재시도한 요청 {retried}"
        lines.append(line)
        if kind in ("upload", "search") and (spans := unavailable_spans(es)):
            lines.append(
                f"   503 구간 {sum(end - s for s, end, _ in spans):.1f}s: "
                + ", ".join(
                    f"+{s - t0:.1f}~+{end - t0:.1f}s({n})" for s, end, n in spans
                )
            )
        segs: list[list] = []
        for e in fails:
            if segs and e["t"] - segs[-1][1] <= 2.0:
                segs[-1][1], segs[-1][2] = e["t"], segs[-1][2] + 1
            else:
                segs.append([e["t"], e["t"], 1])
        if segs:
            lines.append(
                "   실패 구간: "
                + ", ".join(
                    f"+{s - t0:.1f}~+{end - t0:.1f}s({n})" for s, end, n in segs
                )
            )
        for msg, n in Counter(
            (e.get("error") or str(e.get("final_status")))[:90] for e in fails
        ).most_common(4):
            lines.append(f"   {n:4d} × {msg}")

    probes = by("probe")
    if injected and probes:
        outages = write_outages(probes, since=t0)
        if not outages:
            lines.append("-- 쓰기 중단(probe): 없음")
        else:
            recovered = all(end for _, end in outages)
            lines.append(
                "-- 쓰기 중단(probe): 최장 "
                + (
                    f"{max(end - start for start, end in outages):.1f}s"
                    if recovered
                    else "복구 없음"
                )
                + " · 구간 "
                + ", ".join(
                    f"+{start - t0:.1f}~"
                    + (f"+{end - t0:.1f}s({end - start:.1f}s)" if end else "복구 없음")
                    for start, end in outages
                )
            )
    ok_probes = [e for e in probes if e["ok"]]
    changes = [
        (round(e["t"] - t0, 1), e["server"], e["tl"])
        for i, e in enumerate(ok_probes)
        if i == 0
        or (e["server"], e["tl"])
        != (ok_probes[i - 1]["server"], ok_probes[i - 1]["tl"])
    ]
    lines.append(f"   primary·TL 변화: {changes}")
    lines.append(
        f"   VIP: {[(round(e['t'] - t0, 1), e['holder'], e['ping']) for e in by('vip')]}"
    )

    clusters = by("cluster")
    for e in clusters:
        roles = ", ".join(
            f"{m['name']}={m['role']}/{m['state']}/TL{m['timeline']}"
            for m in e["members"]
        )
        lines.append(f"   cluster +{e['t'] - t0:.1f}s {roles}")
    counted = tally(ev)

    st = [e for e in by("status") if e["ok"]]
    load_end = next(e["t"] for e in meta if e.get("phase") == "load_stopped")
    if st:
        peak = {k: max(e[k] for e in st) for k in (*COUNTERS, "error_jobs")}
        zero_at = None
        for e in st:
            if all(e[k] == 0 for k in COUNTERS):
                zero_at = zero_at or (e["t"] if e["t"] >= load_end else None)
            else:
                zero_at = None
        lines.append(
            f"-- 카운터 최대 {peak} · 복제 지연 최대 {max((e.get('lag') or 0) for e in st) / 1e6:.1f}MB"
        )
        lines.append(
            f"   부하 종료 뒤 0 수렴 {(zero_at - load_end) if zero_at else float('nan'):.1f}s · "
            f"복제 상태(마지막) {st[-1].get('replicas')}"
        )

    with psycopg.connect(target.dsn, **target.conn_kwargs()) as conn:
        rows = ledger_rows(conn, target.prefix)
    rec = reconcile(by("upload"), rows)
    nodes = digests(target, wait=60)
    lines.append(
        f"-- 장부: 2xx {rec['acked']} · DB {rec['rows']} · 유실 {len(rec['lost'])} · "
        f"불일치 {len(rec['mismatched'])} · 실패 응답인데 DB에 있음 {len(rec['ghosts'])} · "
        f"중복 {len(rec['duplicates'])} · 장부에 없는 행 {len(rec['unknown'])}"
    )
    lines.append(f"-- 노드: {nodes}")
    lines.append(f"-- 승격: {counted['promotion']}")
    return counted | {"reconcile": rec, "nodes": nodes}, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("label")
        p.add_argument(
            "--nodes",
            required=True,
            help="Patroni 노드 호스트, 쉼표 구분(5432·8008에 직접 붙는다)",
        )
        p.add_argument("--out", default="ha-runs")
        p.add_argument(
            "--api", default=os.environ.get("HA_API", "http://127.0.0.1:18021/api")
        )
    r = sub.choices["run"]
    r.add_argument("--load", type=float, default=120)
    r.add_argument("--settle", type=float, default=900)
    r.add_argument("--inject")
    r.add_argument("--inject-at", type=float, default=40)
    r.add_argument(
        "--blob", action="store_true", help="20MB bytea 연속 쓰기로 복제 지연을 키운다"
    )
    r.add_argument("--user", default=os.environ.get("HA_USER", "ha110"))
    r.add_argument("--password", default=os.environ.get("HA_PASSWORD"))
    a = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL(VIP 경유 DSN)이 필요하다")
    target = Target(
        dsn=dsn,
        api=a.api.rstrip("/"),
        nodes=a.nodes.split(","),
        prefix=f"ha-{a.label}-",
    )
    path = Path(a.out) / f"{a.label}.jsonl"
    if a.cmd == "run":
        if not a.password:
            sys.exit("--password 또는 HA_PASSWORD가 필요하다")
        try:
            asyncio.run(run(a, target, path))
        finally:
            if a.blob:  # 중간에 끊겨도 부하용 테이블을 운영 DB에 남기지 않는다
                with psycopg.connect(dsn, **target.conn_kwargs()) as conn:
                    conn.execute("DROP TABLE IF EXISTS _ha_blob")
    summary, lines = summarize(path, target)
    problems = judge(summary)
    print("\n".join(lines))
    print("== 판정: " + ("통과" if not problems else "위반"))
    for problem in problems:
        print(f"   ✗ {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
