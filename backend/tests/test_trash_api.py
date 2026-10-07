"""휴지통 REST의 소유권·토큰 쓰기 경계·감사 계약."""
from datetime import datetime, timedelta

import psycopg
import pytest
from conftest import login_as, run_embedding_worker, upload_document
from test_groups_api import login_admin
from test_token_access import bearer, issue_token

from openarchive.config import get_settings


@pytest.mark.parametrize("credential", ["session", "token"])
def test_trash_restore_and_purge_pipeline(db_client, migrated_db, credential):
    document = upload_document(db_client, content=b"trash search evidence").json()
    doc_id = document["id"]
    run_embedding_worker(migrated_db)
    headers = {}
    if credential == "token":
        headers = bearer(issue_token(db_client, "alice", scope="read_write")["token"])
        db_client.cookies.clear()
    path = f"/api/documents/{doc_id}"
    assert db_client.delete(path, headers=headers).status_code == 204
    assert db_client.get(path, headers=headers).status_code == 404
    assert db_client.get("/api/documents", headers=headers).json() == []
    def search():
        return db_client.post(
            "/api/search", headers=headers, json={"query": "trash search evidence"}
        ).json()["items"]
    assert search() == []
    listing = db_client.get("/api/documents/trash", headers=headers)
    assert listing.status_code == 200
    item, = listing.json()
    assert set(item) == {"id", "title", "deleted_at", "purge_at"}
    assert (item["id"], item["title"]) == (doc_id, document["title"])
    assert datetime.fromisoformat(item["purge_at"]) - datetime.fromisoformat(
        item["deleted_at"]
    ) == timedelta(days=get_settings().trash_retention_days)
    with psycopg.connect(migrated_db) as conn:
        before = conn.execute(
            "SELECT id, version FROM document_chunks WHERE document_id = %s ORDER BY id",
            (doc_id,),
        ).fetchall()
    restored = db_client.post(path + "/restore", headers=headers)
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT id, version FROM document_chunks WHERE document_id = %s ORDER BY id",
            (doc_id,),
        ).fetchall() == before
    assert restored.status_code == 200
    assert restored.json() == db_client.get("/api/documents", headers=headers).json()[0]
    assert doc_id in {hit["document_id"] for hit in search()}
    assert db_client.post(path + "/restore", headers=headers).status_code == 404
    assert db_client.delete(path, headers=headers).status_code == 204
    assert db_client.delete(path + "?permanent=true", headers=headers).status_code == 204
    assert db_client.get("/api/documents/trash", headers=headers).json() == []
    with psycopg.connect(migrated_db) as conn:
        assert before
        for table in ("documents", "document_chunks", "document_files",
                      "document_versions", "embedding_jobs"):
            key = "id" if table == "documents" else "document_id"
            assert conn.execute(
                f"SELECT count(*) FROM {table} WHERE {key} = %s", (doc_id,)
            ).fetchone() == (0,)
        assert conn.execute(
            "SELECT action, actor_via FROM audit_log WHERE document_id = %s "
            "AND action <> 'document_created' ORDER BY id", (doc_id,)
        ).fetchall() == [(action, credential) for action in (
            "document_trashed", "document_restored", "document_trashed", "document_deleted"
        )]


def test_trash_is_owner_only_and_sorted(db_client, migrated_db):
    first = upload_document(db_client, data={"title": "first"}).json()["id"]
    second = upload_document(db_client, data={"title": "second"}).json()["id"]
    for doc_id in (first, second):
        assert db_client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert [item["id"] for item in db_client.get("/api/documents/trash").json()] == [
        second, first
    ]
    for user in ("bob", "boss"):
        if user == "boss":
            login_admin(db_client, migrated_db)
        else:
            login_as(db_client, user)
        assert db_client.get("/api/documents/trash").json() == []
        assert db_client.post(f"/api/documents/{first}/restore").status_code == 404
        assert db_client.delete(f"/api/documents/{first}?permanent=true").status_code == 404


def test_read_token_cannot_use_any_trash_operation(db_client):
    doc_id = upload_document(db_client).json()["id"]
    headers = bearer(issue_token(db_client, "alice")["token"])
    db_client.cookies.clear()
    path = f"/api/documents/{doc_id}"
    for response in (
        db_client.get("/api/documents/trash", headers=headers),
        db_client.delete(path, headers=headers),
        db_client.post(path + "/restore", headers=headers),
        db_client.delete(path + "?permanent=true", headers=headers),
    ):
        assert response.status_code == 403
        assert response.json() == {"detail": "쓰기 권한이 필요합니다."}
    assert db_client.get(path, headers=headers).status_code == 200


@pytest.mark.parametrize("trashed", [False, True])
def test_permanent_delete_hides_other_owners_even_when_public(
    db_client, migrated_db, trashed
):
    doc_id = upload_document(db_client).json()["id"]
    if trashed:
        assert db_client.delete(f"/api/documents/{doc_id}").status_code == 204
    for user in ("bob", "boss"):
        if user == "boss":
            login_admin(db_client, migrated_db)
        else:
            login_as(db_client, user)
        assert db_client.delete(f"/api/documents/{doc_id}?permanent=true").status_code == 404
        assert db_client.post(f"/api/documents/{doc_id}/restore").status_code == 404
    login_as(db_client, "alice")
    assert db_client.delete(f"/api/documents/{doc_id}?permanent=true").status_code == 204
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM documents WHERE id = %s", (doc_id,)
        ).fetchone() == (0,)
