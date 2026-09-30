"""앱 이미지 진입점 — `init --yes` 뒤 `serve` (#95-e2, ADR-039 개정).

`docker run -e DATABASE_URL=… -e ADMIN_PASSWORD=…` 한 줄로 스키마·첫 관리자·기동까지
가는 것이 이 스크립트의 일이다. 실제 `openarchive` 대신 호출 인자와 PID를 기록하는 가짜를
PATH 맨 앞에 두고 스크립트를 그대로 실행한다 — 검증 대상은 순서·종료 코드·exec 여부이고,
init·serve 자체는 test_cli.py·test_serve.py가 본다.
"""

import os
import subprocess
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT = BACKEND_DIR / "docker-entrypoint.sh"
DSN = "postgresql://app:secret@db:5432/openarchive"

FAKE_OPENARCHIVE = """#!/bin/sh
echo "$$ $*" >> "$CALLS"
if [ "$1" = init ]; then exit "${INIT_EXIT:-0}"; fi
exit 0
"""


def _run(tmp_path: Path, *args: str, env: dict[str, str] | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "openarchive"
    fake.write_text(FAKE_OPENARCHIVE)
    fake.chmod(0o755)
    calls = tmp_path / "calls"
    base = {"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls)}
    process = subprocess.Popen(
        ["/bin/sh", str(ENTRYPOINT), *args],
        env={**base, **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output, _ = process.communicate(timeout=10)
    lines = calls.read_text().splitlines() if calls.exists() else []
    recorded = [(int(pid), command) for pid, _, command in (line.partition(" ") for line in lines)]
    return process, output, recorded


def test_runs_init_then_serve_on_all_interfaces(tmp_path):
    process, output, calls = _run(tmp_path, env={"DATABASE_URL": DSN})

    assert process.returncode == 0, output
    assert [command for _pid, command in calls] == [
        f"init --yes --dsn {DSN}",
        "serve --host 0.0.0.0 --port 8000",
    ]


def test_serve_replaces_the_entrypoint_process(tmp_path):
    # 컨테이너 stop은 PID 1에만 SIGTERM을 보낸다. 셸이 serve를 자식으로 두면 신호가 셸에서
    # 멈춰 워커가 처리 중인 잡을 정리하지 못하고 SIGKILL로 끝난다.
    process, output, calls = _run(tmp_path, env={"DATABASE_URL": DSN})

    (init_pid, _), (serve_pid, _) = calls
    assert serve_pid == process.pid, output
    assert init_pid != process.pid


def test_failed_init_stops_before_serve(tmp_path):
    # 충돌 테이블·확장 부족으로 init이 멈추면 serve도 띄우지 않는다. 띄우면 API startup이
    # 같은 마이그레이션을 다시 시도하다 init보다 알아보기 어려운 트레이스백으로 죽는다.
    process, output, calls = _run(tmp_path, env={"DATABASE_URL": DSN, "INIT_EXIT": "1"})

    assert process.returncode == 1, output
    assert [command for _pid, command in calls] == [f"init --yes --dsn {DSN}"]


def test_missing_database_url_runs_nothing(tmp_path):
    # 비워 두면 init이 기본값(localhost:5433)으로 붙으려 한다. 컨테이너 안의 localhost에는
    # DB가 없으므로 연결 실패 안내가 원인(환경변수 누락)을 가린다.
    process, output, calls = _run(tmp_path)

    assert process.returncode != 0
    assert "DATABASE_URL" in output
    assert calls == []


def test_given_command_runs_alone(tmp_path):
    # `docker run … openarchive create-user bob` 같은 일회성 명령. init을 앞에 붙이면
    # 스키마 확인이 매번 끼어들고, DATABASE_URL 없는 명령(--help)이 막힌다.
    process, output, calls = _run(tmp_path, "openarchive", "create-user", "bob", "--admin")

    assert process.returncode == 0, output
    assert calls == [(process.pid, "create-user bob --admin")]


def test_entrypoint_is_executable():
    # Dockerfile이 COPY로 옮기면 권한 비트가 그대로 간다. 빠지면 컨테이너가 기동 즉시
    # "permission denied"로 죽는다.
    assert os.access(ENTRYPOINT, os.X_OK)
