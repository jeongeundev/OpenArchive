"""`openarchive serve` — API와 워커를 한 명령으로 띄운다 (ADR-039 결정 2 개정).

실제 프로세스를 띄운다. 이 명령이 지켜야 하는 것 — 두 프로세스가 함께 뜨고, Ctrl-C에
함께 내려가며, 한쪽이 못 뜨면 나머지도 남지 않는다 — 은 전부 프로세스 수명주기가
결정하므로 Mock으로는 확인할 수 없다.
"""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 40.0
SHUTDOWN_TIMEOUT = 30.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for(predicate, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _wait_for_api(process: subprocess.Popen, port: int, log_path: Path) -> None:
    """API가 응답할 때까지 기다린다. serve가 먼저 죽으면 기다리지 않고 로그와 함께 실패한다."""

    def ready() -> bool:
        if process.poll() is not None:
            pytest.fail(f"serve가 코드 {process.returncode}으로 먼저 종료됐다:\n{log_path.read_text()}")
        return _api_answers(port)

    assert _wait_for(ready), log_path.read_text()


def _api_answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _group_is_gone(group: int) -> bool:
    """프로세스 그룹에 아무도 남지 않았는가. 반쪽 생존을 잡는 유일한 방법이다."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def serve_env(migrated_db: str) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": migrated_db,
        # 실제 가중치를 내려받지 않는다. 예열이 즉시 끝나 기동 시간이 측정 가능해진다.
        "EMBEDDING_PROVIDER": "fake",
    }


@pytest.fixture
def spawn_serve(tmp_path):
    """`openarchive serve`를 새 세션에서 띄운다. 테스트가 끝나면 그룹째 정리한다.

    start_new_session은 터미널의 Ctrl-C를 재현하기 위한 것이다 — 실제 터미널에서
    SIGINT는 foreground 프로세스 **그룹 전체**에 가고, 그 상황을 그대로 만든다.
    """
    started: list[tuple[subprocess.Popen, int]] = []
    log_path = tmp_path / "serve.log"

    def _spawn(env: dict[str, str], port: int) -> tuple[subprocess.Popen, int, Path]:
        handle = log_path.open("a")
        process = subprocess.Popen(
            [sys.executable, "-m", "app.cli", "serve", "--port", str(port)],
            cwd=BACKEND_DIR,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        group = os.getpgid(process.pid)
        started.append((process, group))
        return process, group, log_path

    yield _spawn

    for process, group in started:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=SHUTDOWN_TIMEOUT)


def test_serve_runs_the_api_and_the_worker_in_one_command(serve_env, spawn_serve):
    """워커를 따로 켜는 것을 잊을 수 없게 만드는 것이 이 명령의 존재 이유다.

    워커가 빠지면 업로드는 성공하는데 검색에 잡히지 않는다 — 에러 없이 조용히 안 된다.
    """
    port = _free_port()
    process, _group, log_path = spawn_serve(serve_env, port)

    _wait_for_api(process, port, log_path)
    assert _wait_for(lambda: "임베딩 워커 기동" in log_path.read_text()), log_path.read_text()

    # 안내가 자식 로그에 묻히면 안 된다. 파이프로 내보낼 때 stdout이 블록 버퍼링되어
    # 안내가 통째로 맨 끝에 밀리는 일이 실제로 있었다 — 그러면 안내로서 쓸모가 없다.
    text = log_path.read_text()
    assert text.index("OpenArchive 실행") < text.index("임베딩 워커 기동"), text


def test_serve_stops_both_processes_on_ctrl_c(serve_env, spawn_serve):
    """Ctrl-C 한 번에 둘 다 내려간다. 워커만 살아남으면 아무도 눈치채지 못한다."""
    port = _free_port()
    process, group, log_path = spawn_serve(serve_env, port)
    _wait_for_api(process, port, log_path)

    os.killpg(group, signal.SIGINT)

    assert process.wait(timeout=SHUTDOWN_TIMEOUT) == 0
    assert _wait_for(lambda: _group_is_gone(group), timeout=SHUTDOWN_TIMEOUT), log_path.read_text()


def test_serve_stops_both_processes_on_sigterm_to_itself(serve_env, spawn_serve):
    """부모만 SIGTERM을 받아도 자식이 고아로 남지 않는다.

    Ctrl-C는 그룹 전체에 가지만 `kill <pid>`·컨테이너 진입점·감독자의 stop은 부모 하나에만
    온다. 부모가 KeyboardInterrupt만 처리하면 uvicorn과 워커가 살아남아, 포트를 쥔 채
    다음 기동을 막고 잡을 계속 집어간다 — 실측으로 확인한 상태다.
    """
    port = _free_port()
    process, group, log_path = spawn_serve(serve_env, port)
    _wait_for_api(process, port, log_path)

    os.kill(process.pid, signal.SIGTERM)

    assert process.wait(timeout=SHUTDOWN_TIMEOUT) == 0
    assert _wait_for(lambda: _group_is_gone(group), timeout=SHUTDOWN_TIMEOUT), log_path.read_text()


def test_serve_exits_when_the_api_cannot_start(serve_env, spawn_serve):
    """반쪽만 도는 상태를 만들지 않는다.

    API가 포트를 못 잡았는데 워커만 남으면, 사용자는 화면이 안 뜨는 이유를 찾다가
    "워커는 돌고 있으니 괜찮겠지"로 오해한다. 함께 내리고 0이 아닌 코드로 끝낸다.
    """
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]

        process, group, log_path = spawn_serve(serve_env, port)

        assert process.wait(timeout=STARTUP_TIMEOUT) != 0, log_path.read_text()
        assert _wait_for(lambda: _group_is_gone(group), timeout=SHUTDOWN_TIMEOUT), (
            log_path.read_text()
        )
