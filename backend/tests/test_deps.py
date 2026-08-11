import pytest
from fastapi import FastAPI, HTTPException, Request

from app.api.deps import get_embedding_provider, require_user_id
from app.embeddings.fake import FakeProvider


async def test_require_user_id_returns_the_authenticated_username():
    assert await require_user_id({"username": "alice"}) == "alice"


async def test_require_user_id_rejects_anonymous_writes():
    with pytest.raises(HTTPException) as error:
        await require_user_id(None)

    assert error.value.status_code == 401
    assert error.value.detail == "로그인이 필요합니다."


def test_get_embedding_provider_returns_the_provider_the_lifespan_stored():
    app = FastAPI()
    app.state.provider = FakeProvider()

    provider = get_embedding_provider(Request({"type": "http", "app": app, "headers": []}))

    assert provider is app.state.provider
