from uuid import uuid4

from app.api.schemas import AuthStatus, RelatedResponse, TagSuggestionsResponse
from app.services.related import (
    IdenticalDocument,
    RelatedDocument,
    RelatedResult,
    TagSuggestion,
    TagSuggestionResult,
)


def test_related_response_accepts_service_dataclasses():
    related_id = uuid4()
    identical_id = uuid4()
    result = RelatedResult(
        items=[RelatedDocument(related_id, "관련", ["opensql"], "related", 0.75)],
        identical=[IdenticalDocument(identical_id, "동일")],
        based_on_version=2,
        reason=None,
    )

    response = RelatedResponse.model_validate(result)

    assert response.items[0].document_id == related_id
    assert response.items[0].kind == "related"
    assert response.identical[0].document_id == identical_id
    assert response.based_on_version == 2


def test_tag_suggestions_response_accepts_service_dataclasses():
    result = TagSuggestionResult(
        items=[TagSuggestion("database", 2)],
        based_on_version=1,
        reason=None,
    )

    response = TagSuggestionsResponse.model_validate(result)

    assert response.items[0].tag == "database"
    assert response.items[0].freq == 2


def test_auth_status_cannot_serialize_session_tokens_or_password_hashes():
    response = AuthStatus(authenticated=True, username="alice", is_admin=False)

    assert response.model_dump() == {
        "authenticated": True,
        "username": "alice",
        "is_admin": False,
    }
