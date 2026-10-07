"""사용자 CLI — login·whoami를 실제 앱과 DB로 관통한다 (#189, ADR-057).

CLI 요청은 lifespan이 돈 앱의 트랜스포트로 간다(`openarchive.client.TRANSPORT`, D9).
`db_client`의 세션 쿠키는 TestClient 객체에만 있어 CLI 요청에는 실리지 않는다 — CLI는
토큰만으로 붙는다.
"""

from __future__ import annotations

import ast
import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import login_as, upload_document
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


# ── doc list · doc show · doc download (step 2) ─────────────────────────────


def put_text(client: TestClient, username: str, title: str, content: str, **extra) -> str:
    """세션으로 텍스트 문서를 만들고 ID를 돌려준다."""
    login_as(client, username)
    response = client.post(
        "/api/documents/text",
        json={"title": title, "content": content, "content_type": "md", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def login_cli(client: TestClient, username: str, *, scope: str = "read_write") -> None:
    assert login(issue_token(client, username, scope=scope)["token"]) == 0


def test_doc_list_matches_web_order_and_visibility(cli, capsys):
    mine_private = put_text(cli, "alice", "내 비공개", "a", visibility="private")
    mine_public = put_text(cli, "alice", "내 공개", "b")
    others_public = put_text(cli, "bob", "밥 공개", "c")
    others_private = put_text(cli, "bob", "밥 비밀 문서", "d", visibility="private")
    login_as(cli, "alice")
    web = [item["id"] for item in cli.get("/api/documents", params={"sort": "updated"}).json()]
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "list"]) == 0

    out = capsys.readouterr().out
    every = {mine_private, mine_public, others_public, others_private}
    listed = [word for word in (line.split()[0] for line in out.splitlines() if line.strip()) if word in every]
    assert listed == web
    assert set(web) == {mine_private, mine_public, others_public}
    assert others_private not in out
    assert "밥 비밀 문서" not in out
    assert "내 공개" in out and "md" in out and "v1" in out


def test_doc_list_shows_processing_labels(cli, migrated_db, capsys):
    states = {
        ("pending", "pending"): "텍스트 인식 중",
        ("failed", "pending"): "텍스트 인식 실패",
        ("done", "pending"): "대기 중",
        ("done", "processing"): "처리 중…",
        ("done", "ready"): "완료",
        ("done", "error"): "실패",
    }
    ids = {key: put_text(cli, "alice", f"상태 {key}", f"본문 {key}") for key in states}
    with psycopg.connect(migrated_db) as conn:
        for (extraction, embedding), document_id in ids.items():
            conn.execute(
                "UPDATE documents SET extraction_status = %s, embedding_status = %s WHERE id = %s",
                (extraction, embedding, document_id),
            )
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "list"]) == 0

    lines = capsys.readouterr().out.splitlines()
    for key, label in states.items():
        (line,) = [line for line in lines if line.startswith(ids[key])]
        assert line.rstrip().endswith(label), (key, line)


def test_doc_list_reads_every_page(cli, migrated_db, capsys):
    login_cli(cli, "alice")
    with psycopg.connect(migrated_db) as conn:
        for index in range(101):
            content = f"문서 {index}"
            conn.execute(
                "INSERT INTO documents (title, content, content_hash, content_type, owner_id)"
                " VALUES (%s, %s, md5(%s), 'md', 'alice')",
                (f"문서 {index}", content, content),
            )
            # 트리거가 남긴 잡은 이 테스트와 무관하다.
    capsys.readouterr()

    assert main(["doc", "list"]) == 0

    out = capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        ids = [str(row[0]) for row in conn.execute("SELECT id FROM documents")]
    assert len(ids) == 101
    assert all(document_id in out for document_id in ids)


def test_doc_list_without_documents(cli, capsys):
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "list"]) == 0

    assert "문서가 없습니다." in capsys.readouterr().out


def test_doc_show_prints_current_or_requested_version(cli, capsys):
    document_id = put_text(cli, "alice", "버전 문서", "첫 번째 본문")
    edited = cli.put(f"/api/documents/{document_id}", json={"content": "두 번째 본문", "version": 1})
    assert edited.status_code == 200
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "show", document_id]) == 0
    assert capsys.readouterr().out.strip() == "두 번째 본문"

    assert main(["doc", "show", document_id, "--version", "1"]) == 0
    assert capsys.readouterr().out.strip() == "첫 번째 본문"


@pytest.mark.parametrize("command", ["show", "download"])
def test_invisible_or_unknown_documents_are_not_found(cli, capsys, tmp_path, command):
    secret = put_text(cli, "bob", "밥 비밀", "비밀 본문", visibility="private")
    login_cli(cli, "alice")
    capsys.readouterr()

    for target in (secret, "00000000-0000-0000-0000-000000000000", "not-a-uuid"):
        argv = ["doc", command, target]
        if command == "download":
            argv += ["-o", str(tmp_path / "out.bin")]
        assert main(argv) == 1
        out = capsys.readouterr().out
        assert "문서를 찾을 수 없습니다" in out
        assert "비밀 본문" not in out
    assert not (tmp_path / "out.bin").exists()


FIXTURES = Path(__file__).parent / "fixtures"


def test_doc_download_saves_identical_original(cli, capsys, tmp_path, monkeypatch):
    original = (FIXTURES / "committee_result.hwp").read_bytes()
    response = upload_document(cli, filename="위원회 결과.hwp", content=original)
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    login_cli(cli, "alice", scope="read")
    capsys.readouterr()

    target = tmp_path / "받은파일.hwp"
    assert main(["doc", "download", document_id, "-o", str(target)]) == 0
    assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()
    assert str(target) in capsys.readouterr().out

    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    assert main(["doc", "download", document_id]) == 0
    assert (workdir / "위원회 결과.hwp").read_bytes() == original

    target.write_bytes(b"keep")
    assert main(["doc", "download", document_id, "-o", str(target)]) == 1
    assert "이미 있는 파일입니다" in capsys.readouterr().out
    assert target.read_bytes() == b"keep"


def test_doc_download_strips_path_from_server_filename(cli, tmp_path, monkeypatch):
    response = upload_document(cli, filename="../../탈출.txt", content=b"escape")
    assert response.status_code == 201, response.text
    login_cli(cli, "alice")
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert main(["doc", "download", response.json()["id"]]) == 0

    saved = [path.name for path in workdir.iterdir()]
    assert len(saved) == 1 and "/" not in saved[0] and saved[0] != ".."
    assert not (tmp_path / "탈출.txt").exists()


def test_doc_download_without_original(cli, capsys, tmp_path):
    document_id = put_text(cli, "alice", "텍스트만", "본문")
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "download", document_id, "-o", str(tmp_path / "x")]) == 1

    assert "원본 파일이 없습니다" in capsys.readouterr().out


def test_read_token_can_list_and_show(cli, capsys):
    document_id = put_text(cli, "alice", "읽기 문서", "읽기 본문")
    login_cli(cli, "alice", scope="read")
    capsys.readouterr()

    assert main(["doc", "list"]) == 0
    assert document_id in capsys.readouterr().out
    assert main(["doc", "show", document_id]) == 0
    assert "읽기 본문" in capsys.readouterr().out
