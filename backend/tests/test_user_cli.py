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
from conftest import login_as, run_embedding_worker, upload_document
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
    # `--token=` — 토큰이 "-"로 시작하면 argparse가 옵션으로 읽는다.
    return main(["login", "--url", URL, f"--token={token}"])


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

    assert main(["login", "--url", URL + "/", f"--token={token}"]) == 0

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


# ── 쓰기: doc upload · edit · restore · tag · delete · trash (step 3) ────────


def write_docx(path: Path, text: str) -> Path:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    document.save(path)
    return path


def detail(client: TestClient, username: str, document_id: str) -> dict:
    login_as(client, username)
    response = client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


def no_prompt(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("확인을 묻지 않아야 한다")

    monkeypatch.setattr("builtins.input", fail)


def test_doc_upload_creates_searchable_document(cli, migrated_db, capsys, tmp_path):
    path = write_docx(tmp_path / "회의록.docx", "출장비 정산 기한은 다음 달 10일입니다.")
    login_cli(cli, "alice")
    capsys.readouterr()

    code = main(
        ["doc", "upload", str(path), "--title", "10월 회의록", "--tag", "회의", "--tag", "인사"]
    )

    assert code == 0
    out = capsys.readouterr().out
    login_as(cli, "alice")
    listed = cli.get("/api/documents").json()
    (item,) = [item for item in listed if item["title"] == "10월 회의록"]
    assert item["id"] in out
    assert sorted(item["tags"]) == ["인사", "회의"]

    run_embedding_worker(migrated_db)
    issued = issue_token(cli, "alice", scope="read")
    found = cli.post(
        "/api/search",
        json={"query": "출장비 정산 기한은 다음 달 10일입니다."},
        headers={"Authorization": f"Bearer {issued['token']}"},
    )
    assert found.status_code == 200
    assert item["id"] in [hit["document_id"] for hit in found.json()["items"]]


class RecordingTransport(httpx.BaseTransport):
    """요청을 기록만 하고 실제 앱으로 그대로 넘긴다 — 응답을 흉내 내지 않는다."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.inner.handle_request(request)


def test_doc_upload_sends_fresh_idempotency_key(cli, monkeypatch, tmp_path):
    recorder = RecordingTransport(client_module.TRANSPORT)
    monkeypatch.setattr(client_module, "TRANSPORT", recorder)
    login_cli(cli, "alice")
    path = tmp_path / "메모.txt"
    path.write_text("메모 본문", encoding="utf-8")

    assert main(["doc", "upload", str(path)]) == 0
    assert main(["doc", "upload", str(path)]) == 0

    keys = [
        request.headers.get("Idempotency-Key")
        for request in recorder.requests
        if request.method == "POST" and request.url.path == "/api/documents"
    ]
    assert len(keys) == 2
    assert all(keys) and keys[0] != keys[1]


def test_doc_upload_missing_file_does_not_call_server(cli, capsys, monkeypatch, tmp_path):
    login_cli(cli, "alice")
    recorder = RecordingTransport(client_module.TRANSPORT)
    monkeypatch.setattr(client_module, "TRANSPORT", recorder)
    capsys.readouterr()
    missing = tmp_path / "없음.docx"

    assert main(["doc", "upload", str(missing)]) != 0

    assert f"파일이 없습니다: {missing}" in capsys.readouterr().out
    assert recorder.requests == []


def test_doc_upload_unsupported_type_shows_server_message(cli, capsys, tmp_path):
    path = tmp_path / "그림.gif"
    path.write_bytes(b"GIF89a")
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "upload", str(path)]) == 1

    assert "지원 형식" in capsys.readouterr().out


def test_doc_edit_creates_new_version(cli, capsys, tmp_path):
    document_id = put_text(cli, "alice", "편집 문서", "처음 본문")
    login_cli(cli, "alice")
    revised = tmp_path / "수정본.txt"
    revised.write_text("고친 본문", encoding="utf-8")
    capsys.readouterr()

    assert main(["doc", "edit", document_id, "--file", str(revised)]) == 0

    assert "v2" in capsys.readouterr().out
    current = detail(cli, "alice", document_id)
    assert current["content"] == "고친 본문"
    assert current["version"] == 2
    assert 2 in [version["version"] for version in current["versions"]]


def test_doc_edit_conflict_does_not_overwrite(cli, capsys, tmp_path):
    document_id = put_text(cli, "alice", "충돌 문서", "처음 본문")
    login_cli(cli, "alice")
    capsys.readouterr()
    assert main(["doc", "show", document_id]) == 0
    revised = tmp_path / "수정본.txt"
    revised.write_text(capsys.readouterr().out + "CLI 수정", encoding="utf-8")
    login_as(cli, "alice")
    web = cli.put(f"/api/documents/{document_id}", json={"content": "웹 수정", "version": 1})
    assert web.status_code == 200

    code = main(["doc", "edit", document_id, "--file", str(revised), "--base-version", "1"])

    assert code == 1
    out = capsys.readouterr().out
    assert "다른 곳에서 먼저 수정되었습니다" in out
    assert "v2" in out
    current = detail(cli, "alice", document_id)
    assert current["content"] == "웹 수정"
    assert current["version"] == 2


def test_doc_restore_makes_new_version_from_old(cli, capsys):
    document_id = put_text(cli, "alice", "되돌릴 문서", "첫 본문")
    for version, content in ((1, "둘째 본문"), (2, "셋째 본문")):
        response = cli.put(
            f"/api/documents/{document_id}", json={"content": content, "version": version}
        )
        assert response.status_code == 200
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "restore", document_id, "1"]) == 0

    assert "v4" in capsys.readouterr().out
    current = detail(cli, "alice", document_id)
    assert current["version"] == 4
    assert current["content"] == "첫 본문"
    assert {1, 2, 3, 4} <= {version["version"] for version in current["versions"]}


@pytest.mark.parametrize("value", ["인사,규정", "인사, 규정", " 인사 ,규정,"])
def test_doc_tag_replaces_tags(cli, capsys, value):
    document_id = put_text(cli, "alice", "태그 문서", "본문", tags=["옛태그"])
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "tag", document_id, "--set", value]) == 0

    assert sorted(detail(cli, "alice", document_id)["tags"]) == ["규정", "인사"]


def test_doc_delete_moves_to_trash_without_prompt(cli, capsys, monkeypatch):
    document_id = put_text(cli, "alice", "지울 문서", "본문")
    login_cli(cli, "alice")
    no_prompt(monkeypatch)
    capsys.readouterr()

    assert main(["doc", "delete", document_id]) == 0
    out = capsys.readouterr().out
    assert "휴지통으로 옮겼습니다" in out
    assert f"openarchive doc trash restore {document_id}" in out

    login_as(cli, "alice")
    assert document_id not in [item["id"] for item in cli.get("/api/documents").json()]
    assert main(["doc", "trash", "list"]) == 0
    out = capsys.readouterr().out
    assert document_id in out and "지울 문서" in out

    assert main(["doc", "trash", "restore", document_id]) == 0
    assert "복원했습니다: 지울 문서" in capsys.readouterr().out
    assert document_id in [item["id"] for item in cli.get("/api/documents").json()]


def test_doc_trash_list_empty(cli, capsys):
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["doc", "trash", "list"]) == 0

    assert "휴지통이 비어 있습니다." in capsys.readouterr().out


def test_doc_delete_permanent_asks_first(cli, capsys, monkeypatch):
    document_id = put_text(cli, "alice", "영구 문서", "본문")
    login_cli(cli, "alice")
    prompts: list[str] = []

    def answer(value: str):
        def ask(prompt: str = "") -> str:
            prompts.append(prompt)
            return value

        return ask

    monkeypatch.setattr("builtins.input", answer("n"))
    assert main(["doc", "delete", document_id, "--permanent"]) == 1
    assert "삭제하면 되돌릴 수 없습니다" in prompts[0]
    assert detail(cli, "alice", document_id)["id"] == document_id

    monkeypatch.setattr("builtins.input", answer("Y"))
    capsys.readouterr()
    assert main(["doc", "delete", document_id, "--permanent"]) == 0
    assert "영구 삭제했습니다." in capsys.readouterr().out
    login_as(cli, "alice")
    assert cli.get(f"/api/documents/{document_id}").status_code == 404
    trash_ids = [item["id"] for item in cli.get("/api/documents/trash").json()]
    assert document_id not in trash_ids


def test_read_token_cannot_write(cli, capsys, monkeypatch, tmp_path):
    document_id = put_text(cli, "alice", "읽기 전용 대상", "원래 본문")
    login_as(cli, "alice")
    before = len(cli.get("/api/documents").json())
    login_cli(cli, "alice", scope="read")
    revised = tmp_path / "수정본.txt"
    revised.write_text("바꾸려는 본문", encoding="utf-8")
    no_prompt(monkeypatch)
    capsys.readouterr()

    for argv in (
        ["doc", "edit", document_id, "--file", str(revised)],
        ["doc", "upload", str(revised)],
        ["doc", "delete", document_id],
    ):
        assert main(argv) == 1, argv
        assert "쓰기 권한이 필요합니다" in capsys.readouterr().out

    current = detail(cli, "alice", document_id)
    assert current["content"] == "원래 본문" and current["version"] == 1
    assert len(cli.get("/api/documents").json()) == before
    assert main(["doc", "list"]) == 0
    assert main(["doc", "show", document_id]) == 0


def test_writes_on_invisible_or_foreign_documents(cli, capsys, monkeypatch, tmp_path):
    secret = put_text(cli, "bob", "밥 비밀", "비밀 본문", visibility="private")
    shared = put_text(cli, "bob", "밥 공개", "공개 본문")
    login_cli(cli, "alice")
    revised = tmp_path / "수정본.txt"
    revised.write_text("남의 문서 수정", encoding="utf-8")
    no_prompt(monkeypatch)
    capsys.readouterr()

    for argv in (
        ["doc", "edit", secret, "--file", str(revised), "--base-version", "1"],
        ["doc", "tag", secret, "--set", "x"],
        ["doc", "delete", secret],
    ):
        assert main(argv) == 1, argv
        assert "문서를 찾을 수 없습니다" in capsys.readouterr().out

    login_as(cli, "alice")
    server = cli.put(f"/api/documents/{shared}", json={"content": "x", "version": 1})
    assert server.status_code == 403
    assert main(["doc", "edit", shared, "--file", str(revised), "--base-version", "1"]) == 1
    assert server.json()["detail"] in capsys.readouterr().out
    assert detail(cli, "bob", shared)["content"] == "공개 본문"
    assert detail(cli, "bob", secret)["content"] == "비밀 본문"


# ── search · ask (step 4) ───────────────────────────────────────────────────


@pytest.fixture
def travel_docs(cli, migrated_db):
    """alice 공개·bob 비공개 문서에 같은 문구를 넣고 임베딩한다."""
    public = put_text(cli, "alice", "출장비 규정", "출장비 정산 기한은 귀임 후 7일입니다.", tags=["규정"])
    secret = put_text(
        cli, "bob", "밥 비밀 출장 메모", "출장비 정산 기한 메모 비공개", visibility="private",
        tags=["메모"],
    )
    run_embedding_worker(migrated_db)
    return {"public": public, "secret": secret}


def test_search_with_token_sees_only_what_the_owner_can(cli, travel_docs, capsys):
    login_cli(cli, "alice", scope="read")
    capsys.readouterr()

    assert main(["search", "출장비 정산 기한"]) == 0

    out = capsys.readouterr().out
    assert "출장비 규정" in out and travel_docs["public"] in out
    assert "밥 비밀 출장 메모" not in out
    assert travel_docs["secret"] not in out

    login_cli(cli, "bob")
    capsys.readouterr()
    assert main(["search", "출장비 정산 기한"]) == 0
    out = capsys.readouterr().out
    assert travel_docs["secret"] in out


def test_search_with_token_sends_filters(cli, travel_docs, monkeypatch, capsys):
    login_cli(cli, "bob")
    sent: list[dict] = []
    inner = client_module.TRANSPORT

    class Recording(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.path == "/api/search":
                sent.append(json.loads(request.read()))
            return inner.handle_request(request)

    monkeypatch.setattr(client_module, "TRANSPORT", Recording())
    capsys.readouterr()

    assert main(["search", "출장비 정산 기한", "--tag", "메모", "--type", "md", "-k", "3"]) == 0

    out = capsys.readouterr().out
    assert sent == [{"query": "출장비 정산 기한", "tags": ["메모"], "content_type": "md", "k": 3}]
    assert travel_docs["secret"] in out
    assert travel_docs["public"] not in out


def test_search_with_token_and_no_results(cli, travel_docs, capsys):
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["search", "출장비", "--tag", "없는태그"]) == 0

    assert "결과가 없습니다." in capsys.readouterr().out


def test_ask_with_token_answers_from_visible_documents(cli, travel_docs, monkeypatch, capsys):
    from openarchive.answers import FakeAnswerProvider
    from openarchive.main import app

    monkeypatch.setattr(app.state, "answer_provider", FakeAnswerProvider())
    login_cli(cli, "alice", scope="read")
    capsys.readouterr()

    assert main(["ask", "출장비 정산 기한은?"]) == 0

    out = capsys.readouterr().out
    assert "근거 기반 답변" in out
    assert "출장비 규정" in out
    assert "밥 비밀 출장 메모" not in out
    assert travel_docs["secret"] not in out


def test_ask_with_token_when_server_disabled(cli, travel_docs, capsys):
    from openarchive.main import app

    assert app.state.answer_provider is None
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["ask", "출장비 정산 기한은?"]) == 1

    assert "답변 생성이 꺼져 있습니다" in capsys.readouterr().out


def test_ask_with_token_without_evidence(cli, travel_docs, monkeypatch, capsys):
    from openarchive.answers import FakeAnswerProvider
    from openarchive.main import app

    monkeypatch.setattr(app.state, "answer_provider", FakeAnswerProvider())
    login_cli(cli, "alice")
    capsys.readouterr()

    assert main(["ask", "출장비", "--tag", "없는태그"]) == 0

    assert "근거로 쓸 문서를 찾지 못했습니다." in capsys.readouterr().out


def test_search_before_login_does_not_touch_the_database(cli, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nowhere")
    import openarchive.cli as cli_module

    def forbidden():
        raise AssertionError("사용자 명령이 DB 설정을 읽었다")

    monkeypatch.setattr(cli_module, "get_settings", forbidden)

    assert main(["search", "출장비"]) == 1
    assert main(["ask", "출장비"]) == 1

    out = capsys.readouterr().out
    assert out.count("로그인이 필요합니다") == 2


@pytest.mark.parametrize("command", ["search", "ask"])
def test_dsn_without_user_is_rejected(cli, monkeypatch, capsys, command):
    login_cli(cli, "alice")

    class Forbidden(httpx.BaseTransport):
        def handle_request(self, request):
            raise AssertionError("--dsn만 있는데 REST로 보냈다")

    monkeypatch.setattr(client_module, "TRANSPORT", Forbidden())
    capsys.readouterr()

    assert main([command, "출장비", "--dsn", "postgresql://x@127.0.0.1:1/y"]) == 2

    assert "--dsn은 --user와 함께 쓰는 운영자 옵션입니다." in capsys.readouterr().out


@pytest.mark.parametrize("command", ["search", "ask"])
def test_user_option_keeps_the_operator_path(cli, monkeypatch, command):
    login_cli(cli, "alice")

    class Forbidden(httpx.BaseTransport):
        def handle_request(self, request):
            raise AssertionError("--user가 있는데 REST로 보냈다")

    monkeypatch.setattr(client_module, "TRANSPORT", Forbidden())
    import openarchive.cli as cli_module

    called: list[str] = []
    monkeypatch.setattr(cli_module, f"run_{command}", lambda **kwargs: called.append(kwargs["username"]) or 0)

    assert main([command, "출장비", "--user", "alice"]) == 0
    assert called == ["alice"]
