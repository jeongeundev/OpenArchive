import pytest
from fastapi import FastAPI, HTTPException, Request

from app.api.deps import (
    get_embedding_provider,
    require_admin,
    require_session_user,
    require_user_id,
    require_write_user_id,
)
from app.embeddings.fake import FakeProvider


async def test_require_user_id_returns_the_authenticated_username():
    assert await require_user_id({"username": "alice"}) == "alice"


async def test_require_user_id_rejects_anonymous_writes():
    with pytest.raises(HTTPException) as error:
        await require_user_id(None)

    assert error.value.status_code == 401
    assert error.value.detail == "로그인이 필요합니다."


async def test_require_write_user_id_accepts_read_write_scope():
    assert await require_write_user_id(
        {"username": "alice", "scope": "read_write"}
    ) == "alice"


async def test_require_write_user_id_rejects_read_scope():
    with pytest.raises(HTTPException) as error:
        await require_write_user_id({"username": "alice", "scope": "read"})

    assert error.value.status_code == 403
    assert error.value.detail != "로그인이 필요합니다."


async def test_require_write_user_id_rejects_anonymous_user():
    with pytest.raises(HTTPException) as error:
        await require_write_user_id(None)

    assert error.value.status_code == 401
    assert error.value.detail == "로그인이 필요합니다."


async def test_require_session_user_accepts_session_credential():
    user = {"username": "alice", "credential": "session"}

    assert await require_session_user(user) is user


async def test_require_session_user_rejects_token_credential():
    with pytest.raises(HTTPException) as error:
        await require_session_user({"username": "alice", "credential": "token"})

    assert error.value.status_code == 403
    assert error.value.detail != "로그인이 필요합니다."


async def test_require_session_user_rejects_anonymous_user():
    with pytest.raises(HTTPException) as error:
        await require_session_user(None)

    assert error.value.status_code == 401
    assert error.value.detail == "로그인이 필요합니다."


@pytest.mark.parametrize(
    ("user", "status_code"),
    [
        ({"is_admin": True, "credential": "session"}, None),
        ({"is_admin": False, "credential": "session"}, 403),
        ({"is_admin": True, "credential": "token"}, 403),
        (None, 401),
    ],
)
async def test_require_admin_requires_an_admin_session(user, status_code):
    if status_code is None:
        assert await require_admin(user) is user
        return

    with pytest.raises(HTTPException) as error:
        await require_admin(user)

    assert error.value.status_code == status_code


def test_get_embedding_provider_returns_the_provider_the_lifespan_stored():
    app = FastAPI()
    app.state.provider = FakeProvider()

    provider = get_embedding_provider(Request({"type": "http", "app": app, "headers": []}))

    assert provider is app.state.provider


async def test_a_write_is_committed_before_the_response_starts(monkeypatch, migrated_db):
    """응답이 나가는 순간 그 요청의 쓰기는 이미 커밋돼 있어야 한다 (#110 C).

    요청 커넥션의 커밋은 `get_conn` 정리(yield 뒤)가 한다. FastAPI의 yield 의존성 기본
    scope("request")는 그 정리를 **응답을 보낸 뒤** 실행한다 — 그러면 클라이언트가
    성공 응답을 받고 곧장 보낸 다음 요청이 아직 커밋되지 않은 상태를 본다. HA 실측에서
    로그인 직후 요청이 30회 중 14회 비인증이었고, primary 직결에서도 재현됐다(커밋이
    WAL flush를 기다리는 부하 중). 커밋이 실패하면 이미 나간 2xx가 거짓이 된다.

    응답 시작 메시지를 가로채 그 순간 **다른 커넥션**에서 세션이 보이는지 확인한다.
    """
    import json

    import psycopg

    from app.config import get_settings
    from app.db import close_pool, get_pool
    from app.main import app
    from app.services.auth import create_user

    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as setup:
        await create_user(setup, "alice", "test-password")

    body = json.dumps({"username": "alice", "password": "test-password"}).encode()
    requests = [{"type": "http.request", "body": body, "more_body": False}]
    visible_at_response_start: list[int] = []
    statuses: list[int] = []

    async def receive():
        return requests.pop(0) if requests else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
            async with await psycopg.AsyncConnection.connect(migrated_db) as other:
                cur = await other.execute("SELECT count(*) FROM sessions")
                visible_at_response_start.append((await cur.fetchone())[0])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    # lifespan(마이그레이션·모델 예열) 없이 풀만 연다 — 로그인 경로가 쓰는 것은 풀뿐이다.
    await get_pool().open()
    try:
        await app(scope, receive, send)
    finally:
        await close_pool()

    assert statuses == [200]
    assert visible_at_response_start == [1]
