# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""문서별 활동 기록 조회를 실제 pgvector DB에서 검증한다 (ADR-055 결정 7 개정)."""

import asyncio
from uuid import UUID

import psycopg
import pytest
from conftest import login_as, upload_document
from test_groups_api import login_admin

from openarchive.services.audit import (
    AUDIT_ACTIONS,
    DOCUMENT_ACTIVITY_OWNER_ACTIONS,
    DOCUMENT_ACTIVITY_PUBLIC_ACTIONS,
    list_document_activity,
)
from openarchive.services.documents import DocumentNotFound


def activity(dsn, document_id, user_id, **kwargs):
    async def call():
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            return await list_document_activity(conn, UUID(document_id), user_id=user_id, **kwargs)

    return asyncio.run(call())


def actions(dsn, document_id, user_id, **kwargs):
    return [row["action"] for row in activity(dsn, document_id, user_id, **kwargs)]


def set_access(client, doc, visibility):
    response = client.put(
        f"/api/documents/{doc}/access",
        json={"visibility": visibility, "users": [], "groups": []},
    )
    assert response.status_code == 200


def edit_text(client, doc, content="수정한 문서 텍스트", version=1):
    response = client.put(f"/api/documents/{doc}", json={"content": content, "version": version})
    assert response.status_code == 200


def share_document(client, doc):
    response = client.post("/api/shares", json={"name": "협업 공유"})
    assert response.status_code == 201
    assert client.put(f"/api/shares/{response.json()['id']}/documents/{doc}").status_code == 204


def test_action_lists_are_known_and_disjoint():
    assert set(DOCUMENT_ACTIVITY_PUBLIC_ACTIONS) <= set(AUDIT_ACTIONS)
    assert set(DOCUMENT_ACTIVITY_OWNER_ACTIONS) <= set(AUDIT_ACTIONS)
    assert not set(DOCUMENT_ACTIVITY_PUBLIC_ACTIONS) & set(DOCUMENT_ACTIVITY_OWNER_ACTIONS)


def test_owner_sees_all_document_actions_newest_first(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    edit_text(db_client, doc)
    set_access(db_client, doc, "private")
    assert db_client.get(f"/api/documents/{doc}/file").status_code == 200

    rows = activity(migrated_db, doc, "alice")
    assert [row["action"] for row in rows] == [
        "original_downloaded", "access_changed", "text_updated", "document_created",
    ]
    assert [row["id"] for row in rows] == sorted((row["id"] for row in rows), reverse=True)
    assert set(rows[0]) == {"id", "occurred_at", "action", "actor", "actor_via", "detail"}
    assert rows[0]["actor"] == "alice"
    assert rows[0]["actor_via"] == "session"
    assert rows[0]["detail"] == {"file_version": 1}


def test_viewer_sees_only_public_actions(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    edit_text(db_client, doc)
    set_access(db_client, doc, "private")
    set_access(db_client, doc, "public")
    assert db_client.get(f"/api/documents/{doc}/file").status_code == 200
    share_document(db_client, doc)
    owner_actions = actions(migrated_db, doc, "alice")
    assert {"access_changed", "original_downloaded", "share_changed"} <= set(owner_actions)

    login_as(db_client, "bob")
    assert actions(migrated_db, doc, "bob") == ["text_updated", "document_created"]


def test_invisible_document_is_not_found_even_for_admin(db_client, migrated_db):
    doc = upload_document(db_client, data={"visibility": "private"}).json()["id"]
    login_as(db_client, "bob")
    login_admin(db_client, migrated_db)
    for user_id in ("bob", "boss", None):
        with pytest.raises(DocumentNotFound):
            activity(migrated_db, doc, user_id)


def test_trashed_document_is_not_found(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    assert db_client.delete(f"/api/documents/{doc}").status_code == 204
    with pytest.raises(DocumentNotFound):
        activity(migrated_db, doc, "alice")


def test_other_documents_do_not_mix(db_client, migrated_db):
    first = upload_document(db_client).json()["id"]
    second = upload_document(db_client, filename="other.txt").json()["id"]
    edit_text(db_client, second)
    assert actions(migrated_db, first, "alice") == ["document_created"]
    assert actions(migrated_db, second, "alice") == ["text_updated", "document_created"]


def test_pages_do_not_overlap_or_skip(db_client, migrated_db):
    doc = upload_document(db_client).json()["id"]
    for version in range(1, 5):
        edit_text(db_client, doc, content=f"버전 {version + 1}", version=version)
    every = [row["id"] for row in activity(migrated_db, doc, "alice")]
    assert len(every) == 5

    pages, before_id = [], None
    while True:
        page = [row["id"] for row in activity(migrated_db, doc, "alice", limit=2, before_id=before_id)]
        if not page:
            break
        assert len(page) <= 2
        pages.extend(page)
        before_id = page[-1]
    assert pages == every
