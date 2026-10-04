"""Barman 백업 복원 재현 (#166, ADR-053).

두 가지를 재현한다. 둘 다 운영 클러스터를 덮지 않고 노드 하나에 격리 인스턴스로 복원한다.

- PITR: 복원 지점을 찍고(`mark`), 그 뒤에 데이터를 바꾼 다음(`break`), 복원 지점으로 복원해
  (`restore --target-name`) 복원본이 복원 지점 전후 스냅샷 사이의 상태인지 대조한다(`verify`).
  삭제한 문서·폐기한 토큰·지운 공유가 되돌아오고, 뒤에 올린 문서는 없어야 한다
- 전체 복원 RPO: `ha_failover.py run`의 부하 중에 Barman 수신을 끊고(`freeze`) 받은 WAL 끝까지
  복원해(`restore`) 끊기 전에 응답 성공한 업로드가 전부 있는지 장부와 대조한다(`rpo`)

복원 명령은 항상 `--target-tli latest --get-wal`이다 — 실측에서 앞의 것이 없으면 switchover 뒤 기동이
실패했고, 뒤의 것이 없으면 닫히지 않은 마지막 세그먼트를 버려 응답 성공한 업로드 120건을 잃었다.

사용 (저장소 루트에서. 앱이 DATABASE_URL과 같은 DB로 떠 있어야 한다. SSH는 --ssh 또는 DR_SSH):
  # PITR
  backend/.venv/bin/python scripts/ha_failover.py run dr1-a --nodes … --load 60   # 원본 판 있는 문서
  backend/.venv/bin/python scripts/dr_restore.py mark dr1 --nodes …
  backend/.venv/bin/python scripts/dr_restore.py break dr1
  backend/.venv/bin/python scripts/dr_restore.py restore dr1 pitr --target-name dr1_before
  ssh -N -L 15433:127.0.0.1:5433 <복원 노드> &          # 복원본은 localhost에만 열린다
  backend/.venv/bin/python scripts/dr_restore.py verify dr1 --dsn …@127.0.0.1:15433/openarchive \\
      --ledger ha-runs/dr1-a.jsonl
  backend/.venv/bin/python scripts/dr_restore.py converge dr1 --dsn …   # 복원본에 앱을 붙인 뒤
  # 전체 복원 RPO
  backend/.venv/bin/python scripts/ha_failover.py run dr1-rpo --nodes … --load 100 --inject-at 60 \\
      --inject 'backend/.venv/bin/python scripts/dr_restore.py freeze'
  backend/.venv/bin/python scripts/dr_restore.py restore dr1 full   # 끊은 채로 — 먼저 thaw하면 끊은 뒤 WAL도 받는다
  backend/.venv/bin/python scripts/dr_restore.py rpo ha-runs/dr1-rpo.jsonl --dsn …
  backend/.venv/bin/python scripts/dr_restore.py thaw
  backend/.venv/bin/python scripts/dr_restore.py drop

상태는 `--out`(기본 ha-runs/)의 `dr-<label>.json`에 남는다. 판정하는 명령의 종료 코드는 통과 0, 위반 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ha_failover import _like_prefix, ledger_rows, reconcile

BARMAN_BIN = "/usr/local/bin/barman"
BARMAN_PG_BIN = "/opt/opensql/bin"  # node4의 PG 유틸리티(`install.sh postgresql`)
JOB_ORDER = ["pending", "processing", "done"]
DOC_FIELDS = ("id", "content_hash", "version", "files", "embedding_status", "chunks", "edges")

# --- 복원 명령 -------------------------------------------------------------------------


def recover_command(
    server: str,
    dest: str,
    target_host: str,
    *,
    target_name: str | None = None,
    target_time: str | None = None,
) -> str:
    """node4에서 `barman` 사용자로 실행할 `barman recover`."""
    if target_name and target_time:
        raise ValueError("--target-name과 --target-time은 하나만 준다")
    argv = [
        BARMAN_BIN,
        "recover",
        "--remote-ssh-command",
        f"ssh opensql@{target_host}",
        "--target-tli",
        "latest",
        "--get-wal",
    ]
    if target_name:
        argv += ["--target-name", target_name]
    if target_time:
        argv += ["--target-time", target_time]
    if target_name or target_time:
        argv += ["--target-action", "promote"]
    return shlex.join([*argv, server, "latest", dest])


def isolation_conf(dest: str, port: int, barman_host: str, server: str) -> str:
    """복원본 `postgresql.auto.conf`에 덧붙일 설정. 같은 키는 뒤에 쓴 값이 이긴다."""
    return f"""
# --- 격리 복원 (scripts/dr_restore.py) — 운영 클러스터와 분리 ---
port = {port}
listen_addresses = 'localhost'
cluster_name = 'restore'
primary_conninfo = ''
primary_slot_name = ''
archive_mode = off
hba_file = '{dest}/pg_hba.conf'
ident_file = '{dest}/pg_ident.conf'
cron.launch_active_jobs = off
restore_command = '/usr/local/bin/barman-wal-restore -P -U barman {barman_host} {server} %f %p'
"""


def check_dest(dest: str) -> str:
    """복원 전에 지울 디렉터리다. 운영 데이터 디렉터리를 가리킬 수 없게 이름을 묶는다."""
    if not posixpath.isabs(dest) or not posixpath.basename(dest).startswith("restore"):
        raise ValueError(f"복원 디렉터리는 절대 경로이고 이름이 restore로 시작해야 한다: {dest}")
    return dest


# --- 판정 ------------------------------------------------------------------------------


def _job_allowed(before: str | None, after: str | None) -> set[str]:
    ends = [s for s in (before, after) if s is not None]
    if len(ends) == 2 and all(s in JOB_ORDER for s in ends):
        return set(JOB_ORDER[JOB_ORDER.index(ends[0]) : JOB_ORDER.index(ends[1]) + 1])
    return set(ends)


def judge_pitr(s1: dict, s2: dict, r: dict) -> list[str]:
    """복원 지점은 스냅샷 S1과 S2 사이에 찍었다. 복원본 R이 그 사이의 상태인지 본다."""
    problems = []
    for key, label in (("tokens", "토큰"), ("shares", "공유"), ("grants", "문서 부여")):
        if s1[key] != s2[key]:
            problems.append(f"{label}이(가) S1·S2 사이에 바뀌었다 — 기대값이 모호하다")
        elif r[key] != s2[key]:
            problems.append(f"{label}: 복원본 {r[key]} ≠ 복원 지점 {s2[key]}"[:400])

    missing = sorted(set(s1["docs"]) & set(s2["docs"]) - set(r["docs"]))
    extra = sorted(set(r["docs"]) - set(s1["docs"]) - set(s2["docs"]))
    if missing:
        problems.append(f"복원 지점에 있던 문서가 복원본에 없다: {missing[:10]}")
    if extra:
        problems.append(f"복원 지점 뒤에 생긴 문서가 복원본에 있다: {extra[:10]}")
    for title, d in r["docs"].items():
        before, after = s1["docs"].get(title), s2["docs"].get(title)
        if before is None or after is None:
            continue
        for k in DOC_FIELDS:
            if before[k] == after[k] and d[k] != after[k]:
                problems.append(f"{title}.{k}: 복원본 {d[k]!r} ≠ 복원 지점 {after[k]!r}"[:400])

    for jid, j in r["jobs"].items():
        allowed = _job_allowed(
            s1["jobs"].get(jid, {}).get("status"), s2["jobs"].get(jid, {}).get("status")
        )
        if allowed and j["status"] not in allowed:
            problems.append(
                f"잡 {jid} {j['title']} {j['kind']}: {j['status']} — S1→S2 경로 {sorted(allowed)} 밖"
            )
    return problems


def judge_rpo(events: list[dict], rows: list[tuple]) -> tuple[dict, list[str]]:
    """`ha_failover.py` 장부의 주입(=수신 끊기) 시작·끝으로 업로드를 셋으로 나눠 대조한다."""
    starts = [e["t"] for e in events if e["kind"] == "inject" and e["phase"] == "start"]
    dones = [e["t"] for e in events if e["kind"] == "inject" and e["phase"] == "done"]
    if not starts or not dones:
        raise ValueError("장부에 끊기(inject) 시작·끝 기록이 없다")
    cut0, cut1 = starts[0], dones[0]
    acked = [e for e in events if e["kind"] == "upload" and e["ok"]]
    must = [e for e in acked if e["t"] < cut0]
    ambiguous = [e for e in acked if cut0 <= e["t"] < cut1]
    after = [e for e in acked if e["t"] >= cut1]
    present = {row[0] for row in rows}
    must_titles = {e["title"] for e in must}
    rec = reconcile(must, [row for row in rows if row[0] in must_titles])
    survivors = [e["t"] for e in must if e["title"] in present]
    summary = {
        "must": len(must),
        "lost": rec["lost"],
        "mismatched": rec["mismatched"] + rec["file_mismatched"],
        "duplicates": rec["duplicates"],
        "ambiguous": len(ambiguous),
        "ambiguous_present": sum(e["title"] in present for e in ambiguous),
        "after": len(after),
        "after_present": sum(e["title"] in present for e in after),
        "gap_s": round(cut0 - max(survivors), 3) if survivors else None,
    }
    problems = []
    if summary["lost"]:
        problems.append(
            f"끊기 전 응답 성공 업로드 {len(summary['lost'])}건 유실: {summary['lost'][:5]}"
        )
    if summary["mismatched"]:
        problems.append(
            f"내용·원본 불일치 {len(summary['mismatched'])}건: {summary['mismatched'][:5]}"
        )
    if summary["duplicates"]:
        problems.append(f"같은 제목 중복 {len(summary['duplicates'])}건")
    if summary["after_present"]:
        problems.append(
            f"끊기 뒤 업로드 {summary['after_present']}건이 복원본에 있다 — 수신이 끊기지 않았다"
        )
    return summary, problems


# --- DB 대조 쿼리 ----------------------------------------------------------------------


def snapshot(conn: psycopg.Connection, prefix: str, username: str) -> dict:
    like = _like_prefix(prefix)
    docs = {
        r[0]: dict(zip(DOC_FIELDS, (str(r[1]), *r[2:]), strict=True))
        for r in conn.execute(
            """
            SELECT d.title, d.id, d.content_hash, d.version,
                   coalesce((SELECT json_agg(json_build_array(f.file_version, f.sha256)
                                             ORDER BY f.file_version)
                             FROM document_files f WHERE f.document_id = d.id), '[]'::json),
                   d.embedding_status,
                   (SELECT count(*) FROM document_chunks ch WHERE ch.document_id = d.id),
                   (SELECT count(*) FROM document_edges e
                     WHERE e.src_document_id = d.id OR e.dst_document_id = d.id)
            FROM documents d WHERE d.title LIKE %s
            """,
            (like,),
        )
    }
    jobs = {
        str(r[0]): {"title": r[1], "kind": r[2], "status": r[3]}
        for r in conn.execute(
            "SELECT j.id, d.title, j.kind, j.status FROM embedding_jobs j"
            " JOIN documents d ON d.id = j.document_id WHERE d.title LIKE %s",
            (like,),
        )
    }
    shares = [
        [str(r[0]), r[1]]
        for r in conn.execute(
            "SELECT s.id, s.name FROM shares s JOIN users u ON u.id = s.owner_user_id"
            " WHERE u.username = %s ORDER BY s.id",
            (username,),
        )
    ]
    # 공유 토큰은 user_id가 없고 share_id로 공유에 매인다
    tokens = [
        [str(r[0]), r[1], str(r[2]) if r[2] else None]
        for r in conn.execute(
            """
            SELECT t.id, t.name, t.share_id FROM api_tokens t
            WHERE t.user_id = (SELECT id FROM users WHERE username = %(u)s)
               OR t.share_id IN (SELECT s.id FROM shares s JOIN users u ON u.id = s.owner_user_id
                                 WHERE u.username = %(u)s)
            ORDER BY t.id
            """,
            {"u": username},
        )
    ]
    grants = [
        [r[0], str(r[1])]
        for r in conn.execute(
            "SELECT d.title, coalesce(g.user_id, g.group_id, g.share_id) AS grantee"
            " FROM document_grants g JOIN documents d ON d.id = g.document_id"
            " WHERE d.title LIKE %s ORDER BY 1, 2",
            (like,),
        )
    ]
    return {
        "t": time.time(),
        "docs": docs,
        "jobs": jobs,
        "tokens": tokens,
        "shares": shares,
        "grants": grants,
    }


def unfinished(conn: psycopg.Connection, prefix: str) -> dict:
    row = conn.execute(
        """
        WITH d AS (SELECT id, version, embedding_status FROM documents WHERE title LIKE %s)
        SELECT (SELECT count(*) FROM embedding_jobs j JOIN d ON d.id = j.document_id
                 WHERE j.status IN ('pending', 'processing')),
               (SELECT count(*) FROM embedding_jobs j JOIN d ON d.id = j.document_id
                 WHERE j.status = 'error'),
               (SELECT count(DISTINCT c.document_id) FROM document_chunks c
                 JOIN d ON d.id = c.document_id WHERE c.version <> d.version),
               (SELECT count(*) FROM d WHERE embedding_status <> 'ready')
        """,
        (_like_prefix(prefix),),
    ).fetchone()
    return dict(zip(("jobs", "error", "inconsistent", "not_ready"), row, strict=True))


# --- 실행 ------------------------------------------------------------------------------


def state_path(a) -> Path:
    return Path(a.out) / f"dr-{a.label}.json"


def load(a) -> dict:
    p = state_path(a)
    return json.loads(p.read_text()) if p.exists() else {}


def save(a, state: dict) -> None:
    p = state_path(a)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str))


def read_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def remote(a, host: str, command: str, *, stdin: str | None = None, check=True) -> str:
    p = subprocess.run(
        [*shlex.split(a.ssh), host, command],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and p.returncode != 0:
        sys.exit(f"[{host}] 실패 (exit {p.returncode}): {command}\n{p.stdout}{p.stderr}")
    return p.stdout + p.stderr


def app_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL(앱과 같은 DB, VIP 경유)이 필요하다")
    return dsn


def take_snapshot(dsn: str, prefix: str, user: str) -> dict:
    # 트랜잭션 안에서 읽는다 — 밖의 단순 SELECT는 OpenProxy가 Replica로 보낸다 (ADR-010)
    with psycopg.connect(dsn) as conn, conn.transaction():
        return snapshot(conn, prefix, user)


def login(a) -> httpx.Client:
    if not a.password:
        sys.exit("--password 또는 HA_PASSWORD가 필요하다")
    c = httpx.Client(base_url=a.api.rstrip("/"), timeout=30)
    c.post("/auth/login", json={"username": a.user, "password": a.password}).raise_for_status()
    return c


def upload(c: httpx.Client, title: str) -> dict:
    body = f"# {title}\n\n복원 시점 검증 문서 {uuid.uuid4().hex}. 백업 복구 PITR 정합성."
    r = c.post(
        "/documents/text",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"title": title, "content": body, "content_type": "md"},
    )
    return {
        "title": title,
        "status": r.status_code,
        "ok": r.is_success,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def leader(nodes: list[str]) -> str:
    for host in nodes:
        try:
            members = httpx.get(f"http://{host}:8008/cluster", timeout=2).json()["members"]
        except (httpx.HTTPError, ValueError, KeyError):
            continue
        for m in members:
            if m.get("role") == "leader":
                return m["host"]
    sys.exit("Patroni leader를 찾지 못했다")


def summary_line(s: dict) -> str:
    pending = sum(j["status"] in ("pending", "processing") for j in s["jobs"].values())
    return (
        f"문서 {len(s['docs'])} · 미완료 잡 {pending} · 토큰 {len(s['tokens'])}"
        f" · 공유 {len(s['shares'])} · 부여 {len(s['grants'])}"
    )


def cmd_mark(a) -> int:
    dsn, prefix, state = app_dsn(), f"ha-{a.label}-", load(a)
    c = login(a)
    personal = c.post("/auth/tokens", json={"name": f"{a.label}-personal", "scope": "read"})
    share = c.post("/shares", json={"name": f"{a.label}-share"})
    personal.raise_for_status()
    share.raise_for_status()
    b = [upload(c, f"{prefix}b-{i:03d}") for i in range(a.b)]
    for title in sorted(take_snapshot(dsn, prefix, a.user)["docs"])[:2]:
        doc_id = take_snapshot(dsn, prefix, a.user)["docs"][title]["id"]
        c.put(f"/shares/{share.json()['id']}/documents/{doc_id}").raise_for_status()
    c.post(
        f"/shares/{share.json()['id']}/tokens", json={"name": f"{a.label}-share-tok"}
    ).raise_for_status()
    s1 = take_snapshot(dsn, prefix, a.user)
    host = leader(a.nodes.split(","))
    name = f"{a.label}_before"
    sql = f"SELECT pg_create_restore_point('{name}') || ' ' || now()"
    rp = remote(
        a,
        a.barman,
        f"sudo -u barman -i {BARMAN_PG_BIN}/psql -X -At -h {host} -U barman"
        f" -d postgres -c {shlex.quote(sql)}",
    ).strip()
    s2 = take_snapshot(dsn, prefix, a.user)
    state.update(
        user=a.user,
        prefix=prefix,
        restore_point=name,
        restore_point_lsn=rp,
        leader=host,
        personal_token=personal.json()["id"],
        share=share.json()["id"],
        b_uploads=b,
        s1=s1,
        s2=s2,
    )
    save(a, state)
    print(f"복원 지점 {name} = {rp} @ {host}")
    print(f"S1: {summary_line(s1)}\nS2: {summary_line(s2)}")
    print(f"B 업로드 응답 {sorted({u['status'] for u in b})}")
    return 0


def cmd_break(a) -> int:
    dsn, state = app_dsn(), load(a)
    prefix = state["prefix"]
    c = login(a)
    titles = sorted(state["s2"]["docs"])
    # 원본 판이 있는 문서(ha_failover 회차 a-)를 먼저 지운다
    victims = (
        [t for t in titles if t.startswith(f"{prefix}a-")]
        + [t for t in titles if not t.startswith(f"{prefix}a-")]
    )[: a.delete]
    for t in victims:
        c.delete(f"/documents/{state['s2']['docs'][t]['id']}").raise_for_status()
    c.delete(f"/auth/tokens/{state['personal_token']}").raise_for_status()
    c.delete(f"/shares/{state['share']}").raise_for_status()
    after = [upload(c, f"{prefix}c-{i:03d}") for i in range(a.c)]
    s3 = take_snapshot(dsn, prefix, state["user"])
    state.update(deleted=victims, c_uploads=after, s3=s3)
    save(a, state)
    print(f"삭제 {len(victims)} · 개인 토큰 폐기 · 공유 삭제 · 뒤 업로드 {len(after)}")
    print(f"S3: {summary_line(s3)}")
    return 0


def cmd_restore(a) -> int:
    dest, pg = check_dest(a.dest), a.pg_home
    state = load(a)
    remote(a, a.target, f"sudo -u opensql {pg}/bin/pg_ctl -D {dest} stop -m fast", check=False)
    remote(a, a.target, f"sudo rm -rf {dest}")
    t0 = time.time()
    recover = recover_command(
        a.server, dest, a.target, target_name=a.target_name, target_time=a.target_time
    )
    print(remote(a, a.barman, f"sudo -u barman -i {recover}").strip())
    t_recovered = time.time()
    remote(
        a,
        a.target,
        f"sudo -u opensql tee -a {dest}/postgresql.auto.conf >/dev/null",
        stdin=isolation_conf(dest, a.port, a.barman, a.server),
    )
    psql = (
        f"sudo -u opensql {pg}/bin/psql -X -At -h {pg}/tmp -p {a.port} -U postgres -d postgres -c"
    )
    remote(
        a,
        a.target,
        f"sudo -u opensql env OPENSQL_LICENSE_PATH={pg}/license/license.xml"
        f" LD_LIBRARY_PATH={pg}/lib {pg}/bin/pg_ctl -D {dest} -l {dest}/restore.log"
        f" -w -t {a.wait} start",
        check=False,
    )
    out = remote(
        a,
        a.target,
        f"for i in $(seq 1 {a.wait}); do {psql} 'SELECT pg_is_in_recovery()' 2>/dev/null"
        f" | grep -qx f && {psql} 'SELECT pg_current_wal_lsn()' && exit 0; sleep 1; done;"
        f" sudo tail -5 {dest}/restore.log; exit 1",
        check=False,
    )
    t_ready = time.time()
    ok = out.strip().splitlines()[-1:] != [] and "/" in out.strip().splitlines()[-1]
    state.setdefault("restores", {})[a.tag] = {
        "recover": recover,
        "t0": t0,
        "recovered_s": round(t_recovered - t0, 1),
        "ready_s": round(t_ready - t0, 1) if ok else None,
        "end_lsn": out.strip()[-40:],
    }
    save(a, state)
    print(
        f"복사 {t_recovered - t0:.0f}초 · 쓰기 가능까지 "
        + (f"{t_ready - t0:.0f}초, 재생 끝 {out.strip()}" if ok else f"실패\n{out}")
    )
    return 0 if ok else 1


def cmd_drop(a) -> int:
    dest = check_dest(a.dest)
    remote(
        a, a.target, f"sudo -u opensql {a.pg_home}/bin/pg_ctl -D {dest} stop -m fast", check=False
    )
    remote(a, a.target, f"sudo rm -rf {dest}")
    print(f"{a.target}:{dest} 정지·삭제")
    return 0


def cmd_verify(a) -> int:
    state = load(a)
    with psycopg.connect(a.dsn) as conn, conn.transaction():
        r = snapshot(conn, state["prefix"], state["user"])
        rows = ledger_rows(conn, state["prefix"]) if a.ledger else []
    problems = judge_pitr(state["s1"], state["s2"], r)
    print(f"복원본: {summary_line(r)}")
    print(
        f"뒤에서 지운 {len(state.get('deleted', []))}건·뒤 업로드 {len(state.get('c_uploads', []))}건"
        " 포함 대조"
    )
    if a.ledger:
        ledger = [e for e in read_jsonl(a.ledger) if e["kind"] == "upload"]
        titles = {e["title"] for e in ledger}
        rec = reconcile(ledger, [row for row in rows if row[0] in titles])
        print(
            "장부 대조: "
            + ", ".join(f"{k}={len(v) if isinstance(v, list) else v}" for k, v in rec.items())
        )
        problems += [
            f"장부 {k}: {v[:5]}"
            for k, v in rec.items()
            if k not in ("acked", "rows", "unknown") and v
        ]
    state.setdefault("verified", {})[a.tag] = {"snapshot": r, "problems": problems}
    save(a, state)
    print("== 판정: " + ("통과" if not problems else f"위반 {len(problems)}"))
    for p in problems[:30]:
        print(f"   ✗ {p}")
    return 1 if problems else 0


def cmd_converge(a) -> int:
    prefix, t0 = load(a)["prefix"], time.time()
    while True:
        with psycopg.connect(a.dsn) as conn, conn.transaction():
            counts = unfinished(conn, prefix)
        print(f"+{time.time() - t0:6.1f}s {counts}", flush=True)
        if not any(counts.values()):
            print(f"== 수렴 {time.time() - t0:.0f}초")
            return 0
        if time.time() - t0 > a.limit:
            print("== 시한 안에 수렴하지 않았다")
            return 1
        time.sleep(3)


def cmd_rpo(a) -> int:
    events = read_jsonl(a.ledger)
    prefix = next(e["prefix"] for e in events if e["kind"] == "meta")
    with psycopg.connect(a.dsn) as conn, conn.transaction():
        rows = ledger_rows(conn, prefix)
    summary, problems = judge_rpo(events, rows)
    print(json.dumps(summary, ensure_ascii=False))
    print("== 판정: " + ("통과" if not problems else f"위반 {len(problems)}"))
    for p in problems:
        print(f"   ✗ {p}")
    return 1 if problems else 0


def cmd_freeze(a) -> int:
    # crond가 매분 receive-wal을 다시 띄우므로 같이 멈춘다. 이미 멈췄으면 --stop이 1로 끝난다
    remote(a, a.barman, "sudo systemctl stop crond")
    print(
        remote(
            a,
            a.barman,
            f"sudo -u barman -i {BARMAN_BIN} receive-wal --stop {a.server}",
            check=False,
        ).strip()
    )
    return 0


def cmd_thaw(a) -> int:
    remote(a, a.barman, "sudo systemctl start crond")
    print("crond 재개 — 다음 분 경계에 receive-wal이 다시 붙는다. `barman check`로 확인")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--ssh",
        default=os.environ.get("DR_SSH", "ssh"),
        help="원격 명령 앞에 붙일 ssh (예: 'ssh -F notes/ha110/ssh_config')",
    )
    ap.add_argument("--barman", default=os.environ.get("DR_BARMAN", "192.168.64.204"))
    ap.add_argument("--server", default="opensql", help="Barman 서버 이름")
    ap.add_argument("--out", default="ha-runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    labelled = {
        name: sub.add_parser(name) for name in ("mark", "break", "restore", "verify", "converge")
    }
    for p in labelled.values():
        p.add_argument("label")
    for name in ("mark", "break"):
        p = labelled[name]
        p.add_argument("--api", default=os.environ.get("HA_API", "http://127.0.0.1:18021/api"))
        p.add_argument("--user", default=os.environ.get("HA_USER", "ha110"))
        p.add_argument("--password", default=os.environ.get("HA_PASSWORD"))
    labelled["mark"].add_argument("--nodes", required=True, help="Patroni 노드, 쉼표 구분")
    labelled["mark"].add_argument("--b", type=int, default=30, help="복원 지점 전 업로드 수")
    labelled["break"].add_argument("--delete", type=int, default=5)
    labelled["break"].add_argument("--c", type=int, default=10, help="복원 지점 뒤 업로드 수")
    drop = sub.add_parser("drop")
    for p in (labelled["restore"], drop):
        p.add_argument(
            "--target",
            default=os.environ.get("DR_TARGET", "192.168.64.202"),
            help="복원 인스턴스를 띄울 노드(Replica 권장)",
        )
        p.add_argument("--dest", default="/home/opensql/restore")
        p.add_argument("--pg-home", default="/home/opensql")
    rs = labelled["restore"]
    rs.add_argument("tag", help="이 복원의 이름(상태 파일 키)")
    rs.add_argument("--target-name")
    rs.add_argument("--target-time")
    rs.add_argument("--port", type=int, default=5433)
    rs.add_argument("--wait", type=int, default=900)
    labelled["verify"].add_argument("tag")
    labelled["verify"].add_argument("--ledger", help="mark 전에 돌린 ha_failover 장부(jsonl)")
    labelled["converge"].add_argument("--limit", type=float, default=900)
    for p in (labelled["verify"], labelled["converge"]):
        p.add_argument("--dsn", required=True, help="복원본 DSN(SSH 터널 경유)")
    rpo = sub.add_parser("rpo")
    rpo.add_argument("ledger", help="수신을 끊은 ha_failover 장부(jsonl)")
    rpo.add_argument("--dsn", required=True)
    sub.add_parser("freeze")
    sub.add_parser("thaw")
    a = ap.parse_args()
    return globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    raise SystemExit(main())
