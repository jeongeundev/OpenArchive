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
