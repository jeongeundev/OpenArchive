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


@pytest.fixture
async def request_pool(monkeypatch, migrated_db):
    """테스트 DB로 연 실제 풀 — lifespan 없이 요청 경로가 쓰는 것만."""
    from app.config import get_settings
    from app.db import close_pool, get_pool

    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    await close_pool()
    pool = get_pool()
    await pool.open()
    yield pool
    await close_pool()


async def test_a_request_that_ends_in_a_db_error_does_not_return_its_connection(request_pool):
    """요청이 DB 오류로 끝나면 그 연결을 닫아 풀이 버리게 한다 (ADR-048 결정 2, #110 B-2).

    OpenProxy가 `BEGIN`에 AllServersDown을 돌려주면 psycopg의 트랜잭션 카운터가 어긋난 채
    연결이 IDLE로 남는다. 풀은 IDLE만 보고 받아들여, 그 연결을 빌리는 요청마다 `transaction()`이
    `AssertionError`를 낸다. 어떤 DB 오류가 연결을 그렇게 만드는지 앱이 가려낼 수 없으므로
    DB 오류로 끝난 연결은 전부 버린다 — 새로 여는 비용이 오염된 연결을 돌려쓰는 위험보다 작다.
    """
    import psycopg

    from app.api.deps import get_conn

    requests = get_conn()
    conn = await anext(requests)
    with pytest.raises(psycopg.errors.SystemError):
        await requests.athrow(psycopg.errors.SystemError("AllServersDown"))

    assert conn.closed


async def test_a_request_that_ends_in_an_http_error_keeps_its_connection(request_pool):
    """404·401 같은 평범한 거절로는 연결을 버리지 않는다 — 요청마다 새 연결을 열게 된다."""
    from app.api.deps import get_conn

    requests = get_conn()
    conn = await anext(requests)
    with pytest.raises(HTTPException):
        await requests.athrow(HTTPException(status_code=404))

    assert not conn.closed


async def test_request_connections_use_keepalive(request_pool):
    """풀의 실제 연결이 keepalive 기본값으로 열렸는지 libpq에서 읽는다 (ADR-048 결정 1).

    인자만 넘기고 libpq가 모르는 키면 연결 자체가 실패하므로, 실제 연결로 확인한다.
    """
    from app.api.deps import get_conn

    requests = get_conn()
    conn = await anext(requests)
    options = {o.keyword: o.val for o in conn.pgconn.info}
    with pytest.raises(StopAsyncIteration):
        await anext(requests)

    assert options[b"keepalives_idle"] == b"30"
    assert options[b"tcp_user_timeout"] == b"60000"
