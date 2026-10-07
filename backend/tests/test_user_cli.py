"""사용자 CLI — login·whoami를 실제 앱과 DB로 관통한다 (#189, ADR-057).

CLI 요청은 lifespan이 돈 앱의 트랜스포트로 간다(`openarchive.client.TRANSPORT`, D9).
`db_client`의 세션 쿠키는 TestClient 객체에만 있어 CLI 요청에는 실리지 않는다 — CLI는
토큰만으로 붙는다.
"""

from __future__ import annotations

import ast
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import login_as
from fastapi.testclient import TestClient
from starlette import testclient as starlette_testclient

import openarchive.client as client_module
from openarchive.cli import main
from openarchive.client import credentials_path

URL = "http://testserver"


def issue_token(client: TestClient, username: str, *, scope: str = "read") -> dict:
    """세션으로 토큰을 발급한다. 세션 쿠키는 TestClient에만 남는다."""
    login_as(client, username)
    response = client.post(
        "/api/auth/tokens", json={"name": f"{username}-{scope}", "scope": scope}
    )
    assert response.status_code == 201
    return response.json()


class AppTransport(httpx.BaseTransport):
    """TestClient의 트랜스포트를 httpx 클라이언트에 잇는다.

    starlette 1.3은 httpx2가 있으면 그것으로 요청을 받는다 — httpx.Client와 타입이 달라 그대로
    꽂을 수 없다. 요청·응답의 모양만 옮기고, 처리는 lifespan이 돈 실제 앱과 DB가 한다.
    """

    def __init__(self, inner) -> None:
        self.inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        forwarded = starlette_testclient.httpx.Request(
            request.method,
            str(request.url),
            headers=list(request.headers.multi_items()),
            content=request.read(),
        )
        answer = self.inner.handle_request(forwarded)
        return httpx.Response(
            answer.status_code,
            headers=list(answer.headers.multi_items()),
            content=answer.read(),
            request=request,
        )


@pytest.fixture
def cli(db_client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setattr(client_module, "TRANSPORT", AppTransport(db_client._transport))
    monkeypatch.setenv("OPENARCHIVE_HOME", str(tmp_path / "home"))
    return db_client


def login(token: str) -> int:
    return main(["login", "--url", URL, "--token", token])


def test_login_saves_owner_only_credentials(cli, capsys):
    token = issue_token(cli, "alice", scope="read_write")["token"]

    assert login(token) == 0

    assert "alice(으)로 로그인했습니다" in capsys.readouterr().out
    path = credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved == {"url": URL, "token": token}


def test_login_strips_trailing_slash(cli):
    token = issue_token(cli, "alice", scope="read")["token"]

    assert main(["login", "--url", URL + "/", "--token", token]) == 0

    assert json.loads(credentials_path().read_text())["url"] == URL


@pytest.mark.parametrize("scope", ["read_write", "read"])
def test_whoami_shows_user_and_scope(cli, capsys, scope):
    token = issue_token(cli, "alice", scope=scope)["token"]
    login(token)
    capsys.readouterr()

    assert main(["whoami"]) == 0

    out = capsys.readouterr().out
    assert "alice" in out
    assert scope in out
    assert "만료 없음" in out
    assert URL in out
    assert token not in out


def test_whoami_shows_expiry_date(cli, capsys):
    login_as(cli, "alice")
    expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    response = cli.post(
        "/api/auth/tokens",
        json={"name": "expiring", "scope": "read", "expires_at": expires_at.isoformat()},
    )
    assert response.status_code == 201
    login(response.json()["token"])
    capsys.readouterr()

    assert main(["whoami"]) == 0

    out = capsys.readouterr().out
    assert expires_at.astimezone().strftime("%Y-%m-%d %H:%M") in out
    assert "만료 없음" not in out


def test_wrong_token_is_rejected_and_not_saved(cli, capsys):
    assert login("not-a-real-token") == 1

    assert "토큰이 올바르지 않습니다" in capsys.readouterr().out
    assert not credentials_path().exists()


def test_failed_login_keeps_previous_credentials(cli, capsys):
    good = issue_token(cli, "alice", scope="read_write")["token"]
    login(good)
    revoked = issue_token(cli, "alice", scope="read")
    assert cli.delete(f"/api/auth/tokens/{revoked['id']}").status_code == 204
    capsys.readouterr()

    assert login(revoked["token"]) == 1

    assert "토큰이 올바르지 않습니다" in capsys.readouterr().out
    assert json.loads(credentials_path().read_text())["token"] == good


def test_whoami_after_revocation_asks_to_log_in_again(cli, capsys):
    issued = issue_token(cli, "alice", scope="read_write")
    login(issued["token"])
    assert cli.delete(f"/api/auth/tokens/{issued['id']}").status_code == 204
    capsys.readouterr()

    assert main(["whoami"]) == 1

    out = capsys.readouterr().out
    assert "토큰이 올바르지 않습니다" in out
    assert "openarchive login" in out


def test_whoami_before_login_asks_to_log_in(cli, capsys):
    assert main(["whoami"]) == 1

    assert "로그인이 필요합니다" in capsys.readouterr().out


def test_login_rejects_url_without_scheme(cli, capsys):
    assert main(["login", "--url", "testserver", "--token", "x"]) == 2

    assert not credentials_path().exists()


def test_user_commands_do_not_read_db_settings(cli, monkeypatch, capsys):
    token = issue_token(cli, "alice", scope="read_write")["token"]
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nowhere")

    def forbidden():
        raise AssertionError("사용자 명령이 DB 설정을 읽었다")

    import openarchive.cli as cli_module

    monkeypatch.setattr(cli_module, "get_settings", forbidden)

    assert login(token) == 0
    assert main(["whoami"]) == 0


@pytest.mark.parametrize("module", ["client.py", "user_cli.py"])
def test_user_cli_modules_do_not_import_the_database(module):
    source = (Path(__file__).parent.parent / "openarchive" / module).read_text()
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for name in imported:
        assert not name.startswith("psycopg"), name
        assert not name.startswith("openarchive.services"), name
        assert not name.startswith("openarchive.db"), name
