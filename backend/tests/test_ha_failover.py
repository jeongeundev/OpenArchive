"""HA 장애 주입 측정기(`scripts/ha_failover.py`)의 판정 로직 (#122).

측정기는 부하를 건 채 장애를 주입하고, 끝난 뒤 "커밋 응답을 받은 쓰기가 전부 있는가·
노드가 같은 데이터를 가졌는가·사용자에게 실패가 보였는가"를 판정한다. 판정이 틀리면
유실을 통과로 적으니, 여기서 판정 함수를 고정한다.
"""

import sys
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import insert_test_document

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ha_failover import (
    Outcome,
    Target,
    call_with_backoff,
    judge,
    ledger_rows,
    next_delay,
    node_digest,
    promotion,
    reconcile,
    tally,
    unavailable_spans,
    uploader,
    write_outage,
)

# --- 클라이언트 백오프: 웹 UI·MCP와 같은 규칙(ADR-048 결정 4) ---------------------------


def test_delay_grows_exponentially_up_to_the_cap_with_full_jitter():
    assert next_delay(0, None, rand=1.0) == 1
    assert next_delay(2, None, rand=1.0) == 4
    assert next_delay(5, None, rand=1.0) == 8  # 상한 8초
    assert next_delay(5, None, rand=0.25) == 2  # 전체 지터: [0, 상한) 균등


def test_retry_after_is_a_floor_not_a_replacement():
    assert next_delay(0, 3, rand=0.5) == 3
    assert next_delay(4, 1, rand=1.0) == 8


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def responses(*items):
    it = iter(items)

    async def send():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return send


async def test_503_and_transport_errors_are_retried_until_success():
    clock = FakeClock()
    send = responses(
        httpx.Response(503, headers={"Retry-After": "1"}),
        httpx.ConnectError("refused"),
        httpx.Response(201),
    )

    out = await call_with_backoff(send, now=clock.now, sleep=clock.sleep, rand=lambda: 1.0)

    assert out.final_status == 201
    assert out.statuses == [503, None, 201]
    assert clock.slept == [1, 2]


async def test_500_is_final_and_not_retried():
    """500은 코드 결함 신호다(ADR-048) — 다시 보내 덮으면 결함이 판정에서 사라진다."""
    clock = FakeClock()
    out = await call_with_backoff(
        responses(httpx.Response(500)), now=clock.now, sleep=clock.sleep, rand=lambda: 1.0
    )

    assert out.final_status == 500
    assert out.statuses == [500]
    assert clock.slept == []


async def test_budget_exhaustion_returns_the_last_failure():
    clock = FakeClock()
    send = responses(*[httpx.Response(503)] * 50)

    out = await call_with_backoff(send, now=clock.now, sleep=clock.sleep, rand=lambda: 1.0)

    assert out.final_status == 503
    assert not out.ok
    assert sum(clock.slept) <= 60  # 예산 60초를 넘겨 기다리지 않는다
    assert clock.slept[:4] == [1, 2, 4, 8]


async def test_transport_error_as_last_attempt_is_reported_as_error():
    clock = FakeClock()
    send = responses(*[httpx.ReadTimeout("slow")] * 50)

    out = await call_with_backoff(send, now=clock.now, sleep=clock.sleep, rand=lambda: 1.0)

    assert out.final_status is None
    assert "ReadTimeout" in out.error
    assert not out.ok


async def test_upload_retries_reuse_the_same_idempotency_key():
    """재시도마다 키가 새로 생기면 모호한 커밋이 중복 문서가 된다(ADR-047) — 장부 판정이 무의미해진다."""
    import asyncio

    stop = asyncio.Event()
    keys: list[str] = []
    replies = iter([503, 201])

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["Idempotency-Key"])
        status = next(replies)
        if status == 201:
            stop.set()
        return httpx.Response(status, headers={"Retry-After": "0"})

    logged: list[dict] = []
    target = Target(dsn="", api="http://api", nodes=[], prefix="ha-t-")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await uploader(lambda kind, **kw: logged.append(kw), stop, client, target)

    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert logged[0]["statuses"] == [503, 201]


# --- 장부 대조 ----------------------------------------------------------------------


def entry(title, ok, sha="h"):
    return {"title": title, "ok": ok, "sha256": sha}


def test_reconcile_finds_lost_mismatched_ghost_duplicate_and_unknown_rows():
    ledger = [
        entry("a", True),  # 정상
        entry("b", True),  # 유실
        entry("c", True),  # 내용 불일치
        entry("d", False),  # 실패 응답인데 DB에 있음(모호한 커밋이 남음)
        entry("e", True),  # 중복 생성
        entry("f", False),  # 실패했고 DB에도 없음 — 정상
    ]
    rows = [
        ("a", "h", "h"),
        ("c", "h", "other"),
        ("d", "h", "h"),
        ("e", "h", "h"),
        ("e", "h", "h"),
        ("z", "h", "h"),  # 장부에 없는 행
    ]

    r = reconcile(ledger, rows)

    assert r == {
        "acked": 4,  # a·b·c·e
        "rows": 6,
        "lost": ["b"],
        "mismatched": ["c"],
        "ghosts": ["d"],
        "duplicates": ["e"],
        "unknown": ["z"],
    }


def test_hash_is_checked_against_both_stored_hash_and_recomputed_content():
    """content_hash만 보면 본문이 깨져도 통과한다 — 본문 sha256도 같이 본다."""
    r = reconcile([entry("a", True)], [("a", "h", "broken")])
    assert r["mismatched"] == ["a"]
    r = reconcile([entry("a", True)], [("a", "broken", "h")])
    assert r["mismatched"] == ["a"]


# --- Patroni 승격 대상 ----------------------------------------------------------------


def cluster(**roles):
    return {"members": [{"name": n, "role": r, "timeline": 1} for n, r in roles.items()]}


def test_promotion_reports_whether_the_new_leader_was_the_sync_standby():
    before = cluster(p1="leader", p2="sync_standby", p3="replica")

    assert promotion(before, cluster(p1="replica", p2="leader", p3="sync_standby")) == {
        "leader_before": "p1",
        "sync_before": "p2",
        "leader_after": "p2",
        "promoted_sync": True,
    }
    assert (
        promotion(before, cluster(p1="replica", p2="replica", p3="leader"))["promoted_sync"]
        is False
    )


def test_promotion_without_leader_change_is_not_judged():
    before = cluster(p1="leader", p2="sync_standby", p3="replica")
    assert promotion(before, before)["promoted_sync"] is None
    # 비동기 클러스터(동기 standby 없음)에서는 판정할 대상이 없다
    assert (
        promotion(cluster(p1="leader", p2="replica"), cluster(p1="replica", p2="leader"))[
            "promoted_sync"
        ]
        is None
    )


# --- 종합 판정 ------------------------------------------------------------------------

CLEAN = {
    "reconcile": {
        "acked": 10,
        "rows": 10,
        "lost": [],
        "mismatched": [],
        "ghosts": [],
        "duplicates": [],
        "unknown": [],
    },
    "raw_500": {"upload": 0, "search": 0},
    "final_failures": {"upload": 0, "search": 0},
    "converged": True,
    "error_jobs": 0,
    "nodes": {"node1": "m", "node2": "m", "node3": "m"},
    "promotion": {"promoted_sync": None},
}


def test_clean_run_passes():
    assert judge(CLEAN) == []


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"reconcile": CLEAN["reconcile"] | {"lost": ["b"]}}, "유실"),
        ({"reconcile": CLEAN["reconcile"] | {"mismatched": ["c"]}}, "불일치"),
        ({"reconcile": CLEAN["reconcile"] | {"ghosts": ["d"]}}, "실패 응답"),
        ({"reconcile": CLEAN["reconcile"] | {"duplicates": ["e"]}}, "중복"),
        ({"raw_500": {"upload": 0, "search": 2}}, "500"),
        ({"final_failures": {"upload": 1, "search": 0}}, "사용자"),
        ({"converged": False}, "수렴"),
        ({"error_jobs": 1}, "error"),
        ({"nodes": {"node1": "m", "node2": "x", "node3": "m"}}, "md5"),
        ({"nodes": {"node1": None, "node2": "m", "node3": "m"}}, "node1"),
        ({"promotion": {"promoted_sync": False}}, "동기"),
    ],
)
def test_each_violation_is_reported(patch, needle):
    problems = judge(CLEAN | patch)
    assert len(problems) == 1
    assert needle in problems[0]


def test_unknown_rows_are_not_a_failure():
    """다른 도구가 같은 접두사로 넣은 행일 수 있다 — 보고는 하되 판정은 장부 기준이다."""
    assert judge(CLEAN | {"reconcile": CLEAN["reconcile"] | {"unknown": ["z"]}}) == []


def test_outcome_ok_means_2xx():
    assert Outcome(final_status=201, error=None, statuses=[201]).ok
    assert not Outcome(final_status=409, error=None, statuses=[409]).ok


# --- DB 대조 쿼리 (실제 컨테이너) --------------------------------------------------------


async def test_ledger_rows_and_digest_cover_only_the_run_prefix(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await insert_test_document(conn, title="ha-run1-00001", content="# 하나\n본문")
        await insert_test_document(conn, title="ha-run1-00002", content="# 둘\n본문")
        await insert_test_document(conn, title="ha-run2-00001", content="# 다른 회차\n본문")

    with psycopg.connect(migrated_db) as conn:
        rows = ledger_rows(conn, "ha-run1-")
        first = node_digest(conn, "ha-run1-")

    assert sorted(r[0] for r in rows) == ["ha-run1-00001", "ha-run1-00002"]
    _, stored, recomputed = next(r for r in rows if r[0] == "ha-run1-00001")
    assert stored == recomputed  # 저장 해시와 본문 재계산이 같은 형식이다

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await conn.execute(
            "UPDATE documents SET content = '# 둘\n고침' WHERE title = 'ha-run1-00002'"
        )
    with psycopg.connect(migrated_db) as conn:
        assert node_digest(conn, "ha-run1-") != first  # 본문이 바뀌면 다이제스트도 바뀐다


async def test_prefix_wildcards_are_escaped(migrated_db: str):
    """접두사의 `_`가 LIKE 와일드카드로 먹으면 다른 회차 행이 섞인다."""
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await insert_test_document(conn, title="ha_a-1", content="x")
        await insert_test_document(conn, title="haXa-1", content="y")

    with psycopg.connect(migrated_db) as conn:
        assert [r[0] for r in ledger_rows(conn, "ha_a-")] == ["ha_a-1"]


# --- 쓰기 중단 구간 -------------------------------------------------------------------


def p(t, ok):
    return {"t": t, "ok": ok, "lat": 0.02}


def test_write_outage_spans_last_success_before_to_first_success_after():
    """이벤트는 끝난 시각으로 찍힌다 — 주입 순간 매달린 probe는 타임아웃 뒤에야 실패로 남는다.
    첫 실패 시각으로 재면 그만큼 중단이 짧게 잡히므로, 앞뒤의 성공으로 경계를 잡는다."""
    probes = [
        p(9.9, True),
        p(10.0, True),
        p(20.6, False),
        p(30.7, False),
        p(40.7, True),
        p(40.8, True),
    ]

    assert write_outage(probes, since=10.0) == (10.0, 40.7)


def test_write_outage_is_none_without_failures_after_injection():
    probes = [p(1.0, False), p(2.0, True), p(12.0, True)]

    assert write_outage(probes, since=10.0) is None


def test_write_outage_without_recovery_has_open_end():
    assert write_outage([p(10.0, True), p(15.0, False)], since=10.0) == (10.0, None)


# --- 기록 → 판정 입력 -----------------------------------------------------------------
# judge()가 맞아도 그 입력을 세는 쪽이 틀리면 유실·500이 통과로 적힌다.


def ev(kind, t, **kw):
    return {"kind": kind, "t": t} | kw


def req(kind, t, statuses, lat=0.1):
    final = statuses[-1]
    return ev(kind, t, ok=final is not None and 200 <= final < 300, lat=lat, statuses=statuses)


def status(t, **counters):
    base = dict.fromkeys(("pending", "processing", "inconsistent", "stale_edges", "not_ready"), 0)
    return ev("status", t, ok=True, error_jobs=0) | base | counters


BEFORE = cluster(p1="leader", p2="sync_standby", p3="replica")
AFTER = cluster(p1="replica", p2="leader", p3="sync_standby")

RUN = [
    ev("meta", 0.0, phase="begin", label="s2-1"),
    ev("cluster", 0.5, **BEFORE),
    req("upload", 1.0, [201]),
    req("upload", 2.0, [503, 503, 201]),  # 백오프가 흡수 — 최종 실패 아님
    req("upload", 3.0, [500]),  # 500은 즉시 최종
    req("search", 1.5, [None, 200]),
    req("search", 2.5, [503, 503]),  # 예산 소진 — 최종 실패
    status(1.0, pending=3),
    status(2.0, error_jobs=1),
    status(9.0),
    ev("cluster", 4.0, **AFTER),
    ev("meta", 5.0, phase="load_stopped"),
    ev("meta", 9.0, phase="end", converged=True),
]


def test_tally_counts_raw_500_and_final_failures_per_kind():
    t = tally(RUN)

    assert t["raw_500"] == {"upload": 1, "search": 0}
    assert t["final_failures"] == {"upload": 1, "search": 1}


def test_tally_reads_convergence_from_the_end_marker():
    assert tally(RUN)["converged"] is True
    assert tally([e for e in RUN if e.get("phase") != "end"])["converged"] is False


def test_tally_keeps_an_error_job_seen_mid_run():
    """error는 끝 상태다 — 한 번이라도 보였으면 마지막 스냅숏과 무관하게 위반이다."""
    assert tally(RUN)["error_jobs"] == 1


def test_tally_judges_promotion_from_first_to_last_cluster_state():
    assert tally(RUN)["promotion"]["promoted_sync"] is True
    assert tally([e for e in RUN if e["kind"] != "cluster"])["promotion"]["promoted_sync"] is None


def test_unavailable_spans_merge_requests_that_saw_503():
    """503은 판정에 넣지 않지만 수와 지속 시간은 따로 남긴다(#119) — 백오프가 흡수한 중단도 보인다."""
    events = [
        req("upload", 10.0, [201]),
        req("upload", 14.0, [503, 201], lat=3.0),  # 11.0~14.0
        req("search", 15.0, [503, 503, 200], lat=2.0),  # 13.0~15.0 — 겹쳐 합쳐진다
        req("upload", 17.5, [503, 201], lat=1.0),  # 16.5~17.5 — 2초 안에 이어져 합쳐진다
        req("search", 30.0, [503, 200], lat=1.0),  # 29.0~30.0 — 떨어져 있다
        req("search", 31.0, [None, 200], lat=1.0),  # 전송 오류는 503이 아니다
    ]

    assert unavailable_spans(events) == [(11.0, 17.5, 3), (29.0, 30.0, 1)]
    assert unavailable_spans([req("upload", 1.0, [201])]) == []
