import pytest
from fastapi import FastAPI, HTTPException, Request

from app.api.deps import get_embedding_provider, optional_user_id, require_user_id
from app.embeddings.fake import FakeProvider


def test_require_user_id_returns_the_header_value():
    assert require_user_id("alice") == "alice"


def test_require_user_id_rejects_a_missing_header_with_an_actionable_message():
    with pytest.raises(HTTPException) as error:
        require_user_id(None)

    assert error.value.status_code == 400
    assert error.value.detail == "X-User-Id 헤더가 필요합니다."


@pytest.mark.parametrize("header", ["alice", None])
def test_optional_user_id_passes_the_header_through_including_absence(header):
    """익명 검색은 owner_id = NULL이 SQL에서 false로 평가되어 public만 남는 데 기댄다.

    그래서 헤더가 없을 때 None이 그대로 내려가야 한다 — 빈 문자열로 바뀌면
    owner_id = '' 비교가 되어 권한 술어의 의미가 달라진다.
    """
    assert optional_user_id(header) is header


def test_get_embedding_provider_returns_the_provider_the_lifespan_stored():
    app = FastAPI()
    app.state.provider = FakeProvider()

    provider = get_embedding_provider(Request({"type": "http", "app": app, "headers": []}))

    assert provider is app.state.provider
