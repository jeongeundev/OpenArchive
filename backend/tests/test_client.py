"""사용자 CLI REST 클라이언트의 재시도·자격증명 (#189 D3·D4).

문서·권한 동작은 test_user_cli.py가 실제 앱과 DB로 관통한다. 여기서 MockTransport를 쓰는
것은 재시도뿐이다 — 503을 실제로 만들 수 없기 때문이다(D9 예외). 대기는 주입한 sleep이
기록해 시간을 쓰지 않는다.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

import openarchive.client as client_module
from openarchive.client import (
    ApiClient,
    ApiError,
    Credentials,
    credentials_path,
    load_credentials,
    save_credentials,
)

CREDS = Credentials(url="http://testserver", token="secret-token")


class Recorder:
    """요청을 기록하고 정해 둔 응답을 차례로 돌려준다. 마지막 응답은 계속 반복한다."""

    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        outcome = self.responses[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def make_client(monkeypatch):
    def factory(responses: list) -> tuple[ApiClient, Recorder, list[float]]:
        recorder = Recorder(responses)
        monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(recorder))
        sleeps: list[float] = []
        return ApiClient(CREDS, sleep=sleeps.append), recorder, sleeps

    return factory


def unavailable(retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(503, headers=headers, json={"detail": "일시적으로"})


def ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def test_get_waits_for_retry_after_seconds_then_succeeds(make_client):
    api, recorder, sleeps = make_client([unavailable("3"), unavailable("3"), ok()])

    response = api.request("GET", "/api/documents")

    assert response.json() == {"ok": True}
    assert len(recorder.requests) == 3
    assert len(sleeps) == 2
    assert all(wait >= 3 for wait in sleeps)


def test_retry_after_http_date_is_honored(make_client):
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)
    api, _, sleeps = make_client([unavailable(when), ok()])

    api.request("GET", "/api/documents")

    assert len(sleeps) == 1
    assert sleeps[0] >= 9


def test_connection_error_is_retried(make_client):
    api, recorder, _ = make_client([httpx.ConnectError("refused"), ok()])

    assert api.request("GET", "/api/auth/me").status_code == 200
    assert len(recorder.requests) == 2


def test_gives_up_after_the_budget(make_client):
    api, recorder, sleeps = make_client([unavailable("30")])

    with pytest.raises(ApiError) as error:
        api.request("GET", "/api/documents")

    assert "서버가 일시적으로 응답하지 못했습니다" in error.value.message
    assert error.value.status == 503
    assert sum(sleeps) <= 60
    assert len(recorder.requests) <= 3


def test_connection_error_after_budget_reports_the_server(make_client):
    api, _, _ = make_client([httpx.ConnectError("refused")])

    with pytest.raises(ApiError) as error:
        api.request("GET", "/api/documents")

    assert error.value.status is None
    assert "서버에 연결하지 못했습니다: http://testserver" in error.value.message


@pytest.mark.parametrize("path", ["/api/search", "/api/ask"])
def test_read_posts_are_retried(make_client, path):
    api, recorder, _ = make_client([unavailable(), ok()])

    api.request("POST", path, json={"query": "q"})

    assert len(recorder.requests) == 2


def test_upload_is_retried_only_with_the_same_idempotency_key(make_client):
    api, recorder, _ = make_client([unavailable(), unavailable(), ok()])

    api.request(
        "POST",
        "/api/documents",
        files={"file": ("a.txt", b"hello")},
        headers={"Idempotency-Key": "key-1"},
    )

    assert len(recorder.requests) == 3
    assert {request.headers["Idempotency-Key"] for request in recorder.requests} == {"key-1"}


def test_upload_without_key_is_not_retried(make_client):
    api, recorder, _ = make_client([unavailable(), ok()])

    with pytest.raises(ApiError):
        api.request("POST", "/api/documents", files={"file": ("a.txt", b"hello")})

    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/documents/00000000-0000-0000-0000-000000000001"),
        ("PUT", "/api/documents/00000000-0000-0000-0000-000000000001/tags"),
        ("DELETE", "/api/documents/00000000-0000-0000-0000-000000000001"),
        ("POST", "/api/documents/00000000-0000-0000-0000-000000000001/restore"),
        ("POST", "/api/documents/00000000-0000-0000-0000-000000000001/versions/1/restore"),
    ],
)
def test_other_writes_are_not_retried(make_client, method, path):
    api, recorder, sleeps = make_client([unavailable(), ok()])

    with pytest.raises(ApiError) as error:
        api.request(method, path, json={})

    assert len(recorder.requests) == 1
    assert sleeps == []
    assert "서버가 일시적으로 응답하지 못했습니다" in error.value.message


def test_retry_notice_is_printed_once_to_stderr(make_client, capsys):
    api, _, _ = make_client([unavailable(), unavailable(), unavailable(), ok()])

    api.request("GET", "/api/documents")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("다시 시도하는 중입니다") == 1


def test_every_request_carries_the_bearer_token(make_client):
    api, recorder, _ = make_client([ok()])

    api.request("GET", "/api/auth/me")

    assert recorder.requests[0].headers["Authorization"] == "Bearer secret-token"


def test_error_detail_is_passed_through(make_client):
    api, _, _ = make_client([httpx.Response(404, json={"detail": "문서를 찾을 수 없습니다."})])

    with pytest.raises(ApiError) as error:
        api.request("GET", "/api/documents/x")

    assert error.value.message == "문서를 찾을 수 없습니다."
    assert error.value.status == 404


def test_unauthorized_points_to_login(make_client):
    api, _, _ = make_client([httpx.Response(401, json={"detail": "로그인이 필요합니다."})])

    with pytest.raises(ApiError) as error:
        api.request("GET", "/api/documents")

    assert "토큰이 올바르지 않습니다" in error.value.message
    assert "openarchive login" in error.value.message


def test_validation_errors_are_joined(make_client):
    body = {"detail": [{"msg": "too short"}, {"msg": "missing"}]}
    api, _, _ = make_client([httpx.Response(422, json=body)])

    with pytest.raises(ApiError) as error:
        api.request("PUT", "/api/documents/x/tags", json={})

    assert error.value.message.startswith("요청 값이 올바르지 않습니다:")
    assert "too short" in error.value.message and "missing" in error.value.message


def test_credentials_are_saved_owner_only(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OPENARCHIVE_HOME", str(home))

    assert load_credentials() is None
    save_credentials(CREDS)

    path = credentials_path()
    assert path.parent == home.resolve()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert load_credentials() == CREDS


def test_saving_over_a_loose_file_tightens_it(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENARCHIVE_HOME", str(tmp_path))
    path = credentials_path()
    path.write_text("{}")
    path.chmod(0o644)
    tmp_path.chmod(0o755)

    save_credentials(Credentials(url="http://other", token="t2"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # 있던 디렉터리의 권한은 건드리지 않는다 (D4)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
    assert load_credentials() == Credentials(url="http://other", token="t2")
