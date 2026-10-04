"""백업 복원 재현 도구(`scripts/dr_restore.py`)의 판정·명령 생성 (#166, ADR-053).

도구는 복원 지점 전후 스냅샷과 복원본을 대조하고(PITR), 수신을 끊은 시각 전후 업로드
장부와 복원본을 대조한다(전체 복원 RPO). 판정이 틀리면 유실을 통과로 적고, 복원 명령에서
옵션 하나가 빠지면 실측에서 확인한 실패(타임라인·`.partial`·운영 클러스터 접속)가 그대로
재현된다. 그래서 둘 다 여기서 고정한다.
"""

import shlex
import sys
from pathlib import Path

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs

from openarchive.embeddings.fake import FakeProvider

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.dr_restore import (
    check_dest,
    isolation_conf,
    judge_pitr,
    judge_rpo,
    recover_command,
    snapshot,
    unfinished,
)

# --- 복원 명령: 실측에서 빠뜨려 실패한 옵션이 항상 들어간다 ------------------------------


def test_recover_always_follows_the_latest_timeline_and_fetches_partial_wal():
    argv = shlex.split(recover_command("opensql", "/home/opensql/restore", "192.168.64.202"))
    # --target-tli가 없으면 백업 타임라인 WAL만 복사돼 switchover 뒤 기동이 실패했다
    assert argv[argv.index("--target-tli") + 1] == "latest"
    # --get-wal이 없으면 닫히지 않은 마지막 세그먼트를 버려 응답 성공 120건을 잃었다
    assert "--get-wal" in argv
    assert argv[argv.index("--remote-ssh-command") + 1] == "ssh opensql@192.168.64.202"
    assert argv[-3:] == ["opensql", "latest", "/home/opensql/restore"]
    assert "--target-name" not in argv and "--target-action" not in argv


@pytest.mark.parametrize(
    ("kw", "flag", "value"),
    [
        ({"target_name": "before_bulk_delete"}, "--target-name", "before_bulk_delete"),
        ({"target_time": "2026-10-04 11:35:00+09"}, "--target-time", "2026-10-04 11:35:00+09"),
    ],
)
def test_point_in_time_target_is_promoted_not_paused(kw, flag, value):
    argv = shlex.split(recover_command("opensql", "/home/opensql/restore", "h", **kw))
    assert argv[argv.index(flag) + 1] == value
    # 기본값 pause면 대상 지점에서 읽기 전용으로 멈춰 워커가 잡을 회수하지 못한다
    assert argv[argv.index("--target-action") + 1] == "promote"


def test_name_and_time_targets_are_exclusive():
    with pytest.raises(ValueError):
        recover_command("opensql", "/d/restore", "h", target_name="a", target_time="b")


def conf_dict(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def test_isolation_conf_detaches_the_copy_from_the_running_cluster():
    conf = conf_dict(isolation_conf("/home/opensql/restore", 5433, "192.168.64.204", "opensql"))
    # 백업 안의 Patroni 설정은 운영 노드·운영 슬롯·운영 데이터 디렉터리를 가리킨다
    assert conf["primary_conninfo"] == "''"
    assert conf["primary_slot_name"] == "''"
    assert conf["hba_file"] == "'/home/opensql/restore/pg_hba.conf'"
    assert conf["ident_file"] == "'/home/opensql/restore/pg_ident.conf'"
    assert conf["archive_mode"] == "off"
    assert conf["port"] == "5433"
    assert conf["listen_addresses"] == "'localhost'"
    assert conf["cron.launch_active_jobs"] == "off"


def test_isolation_conf_rewrites_restore_command_with_absolute_path_and_ip():
    """Barman은 자기 hostname과 PATH 밖 명령 이름을 넣는다 — 대상 노드에서 둘 다 못 찾는다."""
    conf = conf_dict(isolation_conf("/home/opensql/restore", 5433, "192.168.64.204", "opensql"))
    assert conf["restore_command"] == (
        "'/usr/local/bin/barman-wal-restore -P -U barman 192.168.64.204 opensql %f %p'"
    )


@pytest.mark.parametrize(
    "dest", ["/home/opensql/data", "/home/opensql", "/", "restore", "/home/opensql/restore/../data"]
)
def test_destination_that_could_be_a_live_data_dir_is_refused(dest):
    """복원 전에 대상 디렉터리를 지운다 — 운영 데이터 디렉터리를 가리키면 안 된다."""
    with pytest.raises(ValueError):
        check_dest(dest)


def test_restore_named_destination_is_accepted():
    assert check_dest("/home/opensql/restore") == "/home/opensql/restore"
    assert check_dest("/home/opensql/restore166") == "/home/opensql/restore166"


# --- PITR 판정: 복원본 = 복원 지점의 상태 -----------------------------------------------


def doc(**kw):
    return {
        "id": "id-" + kw.pop("key", "x"),
        "content_hash": "h",
        "version": 1,
        "files": [],
        "embedding_status": "ready",
        "chunks": 2,
        "edges": 1,
    } | kw


def snap(docs=None, jobs=None, tokens=None, shares=None, grants=None):
    return {
        "docs": docs
        if docs is not None
        else {"ha-x-a-1": doc(key="a1"), "ha-x-b-1": doc(key="b1")},
        "jobs": jobs
        if jobs is not None
        else {"1": {"title": "ha-x-b-1", "kind": "embed", "status": "done"}},
        "tokens": tokens if tokens is not None else [["t1", "personal", None], ["t2", "s", "sh1"]],
        "shares": shares if shares is not None else [["sh1", "share"]],
        "grants": grants if grants is not None else [["ha-x-a-1", "sh1"]],
    }


def test_restored_copy_equal_to_the_restore_point_passes():
    s1, s2 = snap(), snap()
    assert judge_pitr(s1, s2, snap()) == []


@pytest.mark.parametrize(
    ("restored", "needle"),
    [
        (snap(docs={"ha-x-b-1": doc(key="b1")}), "ha-x-a-1"),  # 복원 지점 뒤 삭제가 되돌아가지 않음
        (snap(docs=snap()["docs"] | {"ha-x-c-1": doc(key="c1")}), "ha-x-c-1"),  # 뒤 업로드가 남음
        (snap(tokens=[["t2", "s", "sh1"]]), "토큰"),
        (snap(shares=[]), "공유"),
        (snap(grants=[]), "부여"),
        (snap(docs=snap()["docs"] | {"ha-x-a-1": doc(key="a1", content_hash="z")}), "content_hash"),
        (snap(docs=snap()["docs"] | {"ha-x-a-1": doc(key="a1", files=[[1, "s"]])}), "files"),
    ],
)
def test_each_divergence_from_the_restore_point_is_reported(restored, needle):
    problems = judge_pitr(snap(), snap(), restored)
    assert len(problems) == 1
    assert needle in problems[0]


def test_field_that_moved_between_the_two_snapshots_is_not_judged():
    """복원 지점은 S1과 S2 사이 어딘가다 — 그 사이에 바뀐 값은 어느 쪽이든 맞을 수 있다."""
    s1 = snap(docs={"ha-x-a-1": doc(key="a1", embedding_status="pending", chunks=0)})
    s2 = snap(docs={"ha-x-a-1": doc(key="a1")})
    r = snap(docs={"ha-x-a-1": doc(key="a1", embedding_status="processing", chunks=0)})
    clean = {"grants": [], "jobs": {}}
    assert judge_pitr(s1 | clean, s2 | clean, r | clean) == []
    # 두 스냅샷에서 같았던 값은 여전히 판정한다
    r_bad = snap(
        docs={"ha-x-a-1": doc(key="a1", embedding_status="processing", chunks=0, version=2)}
    )
    assert any("version" in p for p in judge_pitr(s1 | clean, s2 | clean, r_bad | clean))


def job(status):
    return {"title": "ha-x-b-1", "kind": "embed", "status": status}


@pytest.mark.parametrize(
    ("before", "after", "restored", "ok"),
    [
        ("pending", "done", "processing", True),  # 복원 지점이 처리 도중이었다
        ("pending", "done", "pending", True),
        ("pending", "processing", "done", False),  # 복원 지점보다 앞선 상태
        ("pending", "done", "error", False),
        ("done", "done", "pending", False),
    ],
)
def test_job_status_must_lie_on_the_path_between_the_snapshots(before, after, restored, ok):
    s1, s2 = snap(jobs={"1": job(before)}), snap(jobs={"1": job(after)})
    problems = judge_pitr(s1, s2, snap(jobs={"1": job(restored)}))
    assert (problems == []) is ok


def test_credentials_changed_between_snapshots_make_the_expectation_ambiguous():
    s1, s2 = snap(tokens=[]), snap()
    problems = judge_pitr(s1, s2, snap())
    assert any("모호" in p for p in problems)


# --- 전체 복원 RPO: 수신을 끊기 전에 응답 성공한 업로드는 전부 있어야 한다 ----------------


def up(t, title, sha="s", ok=True, **kw):
    return {"kind": "upload", "t": t, "title": title, "sha256": sha, "ok": ok} | kw


EVENTS = [
    {"kind": "meta", "phase": "begin", "t": 0.0},
    up(1.0, "ha-r-1"),
    up(9.8, "ha-r-2"),
    up(9.9, "ha-r-x", ok=False),
    {"kind": "inject", "phase": "start", "t": 10.0},
    up(11.0, "ha-r-3"),
    {"kind": "inject", "phase": "done", "t": 12.0},
    up(13.0, "ha-r-4"),
]


def row(title, sha="s"):
    return (title, sha, sha, 1, [])


def test_rpo_passes_when_every_upload_acked_before_the_cut_survives():
    summary, problems = judge_rpo(EVENTS, [row("ha-r-1"), row("ha-r-2"), row("ha-r-3")])
    assert problems == []
    assert summary["must"] == 2 and summary["lost"] == []
    assert summary["ambiguous"] == 1 and summary["ambiguous_present"] == 1
    assert summary["after"] == 1 and summary["after_present"] == 0
    assert summary["gap_s"] == pytest.approx(0.2)  # 마지막 생존 업로드 → 끊기 시작


def test_rpo_reports_lost_uploads_and_the_gap_to_the_last_survivor():
    summary, problems = judge_rpo(EVENTS, [row("ha-r-1")])
    assert summary["lost"] == ["ha-r-2"]
    assert summary["gap_s"] == pytest.approx(9.0)
    assert any("유실" in p for p in problems)


def test_rpo_reports_content_mismatch():
    _, problems = judge_rpo(EVENTS, [row("ha-r-1"), row("ha-r-2", sha="other")])
    assert any("불일치" in p for p in problems)


def test_upload_after_the_cut_in_the_copy_means_the_cut_did_not_happen():
    _, problems = judge_rpo(EVENTS, [row("ha-r-1"), row("ha-r-2"), row("ha-r-4")])
    assert any("끊기" in p for p in problems)


def test_rpo_needs_the_cut_events_in_the_ledger():
    with pytest.raises(ValueError):
        judge_rpo([e for e in EVENTS if e["kind"] != "inject"], [])


# --- DB 스냅샷 (실제 컨테이너) ----------------------------------------------------------


async def test_snapshot_covers_the_run_prefix_and_the_users_credentials(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        a = await insert_test_document(conn, title="ha-dr1-a-1", content="# 하나\n본문")
        await insert_test_document(conn, title="ha-dr2-a-1", content="# 다른 회차\n본문")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        users = {
            name: conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
                (name,),
            ).fetchone()[0]
            for name in ("dr", "other")
        }
        share = conn.execute(
            "INSERT INTO shares (owner_user_id, name) VALUES (%s, 'dr-share') RETURNING id",
            (users["dr"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO document_grants (document_id, share_id) VALUES (%s, %s)", (a, share)
        )
        for user_id, share_id, name, scope in [
            (users["dr"], None, "personal", "read"),
            (None, share, "share-tok", "read"),  # 공유 토큰은 user_id가 없다
            (users["other"], None, "not-mine", "read"),
        ]:
            conn.execute(
                "INSERT INTO api_tokens (user_id, share_id, name, token_hash, scope)"
                " VALUES (%s, %s, %s, %s, %s)",
                (user_id, share_id, name, f"hash-{name}", scope),
            )

    with psycopg.connect(migrated_db) as conn:
        s = snapshot(conn, "ha-dr1-", "dr")

    assert sorted(s["docs"]) == ["ha-dr1-a-1"]
    d = s["docs"]["ha-dr1-a-1"]
    assert d["id"] == str(a) and d["version"] == 1 and d["files"] == []
    assert [j["status"] for j in s["jobs"].values()] == ["pending"]  # 트리거가 만든 잡
    assert sorted(t[1] for t in s["tokens"]) == ["personal", "share-tok"]
    assert s["shares"] == [[str(share), "dr-share"]]
    assert s["grants"] == [["ha-dr1-a-1", str(share)]]


async def test_unfinished_counts_only_the_run_prefix(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await insert_test_document(conn, title="ha-dr1-a-1", content="# 하나\n본문")
        await insert_test_document(conn, title="ha-dr2-a-1", content="# 둘\n본문")
    with psycopg.connect(migrated_db) as conn:
        counts = unfinished(conn, "ha-dr1-")
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await process_all_embedding_jobs(conn, FakeProvider())  # 실제 워커로 잡을 비운다
        await insert_test_document(conn, title="ha-dr2-a-2", content="# 셋\n본문")
    with psycopg.connect(migrated_db) as conn:
        done = unfinished(conn, "ha-dr1-")

    assert counts["jobs"] == 1 and counts["not_ready"] == 1
    # 다른 회차의 미완료 잡은 세지 않는다
    assert done == {"jobs": 0, "error": 0, "inconsistent": 0, "not_ready": 0}
