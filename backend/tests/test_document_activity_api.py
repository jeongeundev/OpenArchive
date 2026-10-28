# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""문서 활동 기록 API (ADR-055 결정 7 개정)."""

from conftest import login_as, upload_document
from fastapi.testclient import TestClient
from test_document_activity import edit_text, set_access, share_document
from test_groups_api import login_admin
from test_share_access import FORBIDDEN_DETAIL, issue_share_token
from test_token_access import bearer, issue_token

from openarchive.services.audit import DOCUMENT_ACTIVITY_OWNER_ACTIONS

ENTRY_KEYS = {"id", "occurred_at", "action", "actor", "actor_via", "detail"}


def activity(client: TestClient, doc: str, **kwargs):
    return client.get(f"/api/documents/{doc}/activity", **kwargs)


def busy_document(client: TestClient) -> str:
    """alice 문서에 열람자 전원 동작과 소유자 전용 동작을 고루 남긴다."""
    doc = upload_document(client).json()["id"]
    edit_text(client, doc)
    set_access(client, doc, "private")
    set_access(client, doc, "public")
    assert client.get(f"/api/documents/{doc}/file").status_code == 200
    share_document(client, doc)
    return doc


def test_owner_gets_every_action_with_entry_keys(db_client: TestClient):
    doc = busy_document(db_client)

    response = activity(db_client, doc)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "next_before_id"}
    items = body["items"]
    assert all(set(item) == ENTRY_KEYS for item in items)
    assert [item["id"] for item in items] == sorted((item["id"] for item in items), reverse=True)
    assert {"access_changed", "share_changed", "original_downloaded"} <= {
        item["action"] for item in items
    }
    assert items[-1]["action"] == "document_created"
    assert items[-1]["actor"] == "alice"
    assert items[-1]["actor_via"] == "session"
    assert body["next_before_id"] is None


def test_viewer_does_not_see_owner_only_actions(db_client: TestClient):
    doc = busy_document(db_client)
    login_as(db_client, "bob")

    response = activity(db_client, doc)

    assert response.status_code == 200
    seen = [item["action"] for item in response.json()["items"]]
    assert seen == ["text_updated", "document_created"]
    assert not set(seen) & set(DOCUMENT_ACTIVITY_OWNER_ACTIONS)


def test_invisible_document_is_404_like_the_document_path(db_client: TestClient):
    doc = upload_document(db_client, data={"visibility": "private"}).json()["id"]
    login_as(db_client, "bob")

    response = activity(db_client, doc)

    assert response.status_code == 404
    assert response.json() == db_client.get(f"/api/documents/{doc}").json()


def test_admin_who_cannot_see_the_document_gets_404(db_client: TestClient, migrated_db: str):
    doc = upload_document(db_client, data={"visibility": "private"}).json()["id"]
    login_admin(db_client, migrated_db)

    assert activity(db_client, doc).status_code == 404


def test_anonymous_is_401(db_client: TestClient):
    doc = upload_document(db_client).json()["id"]
    db_client.cookies.clear()

    assert activity(db_client, doc).status_code == 401


def test_share_token_is_rejected(db_client: TestClient):
    doc = upload_document(db_client).json()["id"]
    share = db_client.post("/api/shares", json={"name": "B사"}).json()
    assert db_client.put(f"/api/shares/{share['id']}/documents/{doc}").status_code == 204
    token = issue_share_token(db_client, share["id"])
    db_client.cookies.clear()

    response = activity(db_client, doc, headers=bearer(token["token"]))

    assert response.status_code == 403
    assert response.json()["detail"] == FORBIDDEN_DETAIL


def test_user_api_token_reads_activity(db_client: TestClient):
    doc = upload_document(db_client).json()["id"]
    token = issue_token(db_client, "alice")
    db_client.cookies.clear()

    response = activity(db_client, doc, headers=bearer(token["token"]))

    assert response.status_code == 200
    assert [item["action"] for item in response.json()["items"]] == ["document_created"]


def test_limit_out_of_range_is_422(db_client: TestClient):
    doc = upload_document(db_client).json()["id"]

    for limit in (0, 201):
        assert activity(db_client, doc, params={"limit": limit}).status_code == 422


def test_pages_follow_next_before_id(db_client: TestClient):
    doc = upload_document(db_client).json()["id"]
    for version in range(1, 5):
        edit_text(db_client, doc, content=f"버전 {version + 1}", version=version)
    every = [item["id"] for item in activity(db_client, doc).json()["items"]]
    assert len(every) == 5

    pages, params = [], {"limit": 2}
    while True:
        body = activity(db_client, doc, params=params).json()
        pages.extend(item["id"] for item in body["items"])
        if body["next_before_id"] is None:
            break
        assert body["next_before_id"] == body["items"][-1]["id"]
        params = {"limit": 2, "before_id": body["next_before_id"]}
    assert pages == every
