import asyncio

import psycopg
from conftest import process_all_embedding_jobs
from fastapi.testclient import TestClient

from app.embeddings.fake import FakeProvider


def upload(client: TestClient, content: str = "OpenSQL status") -> dict:
    response = client.post(
        "/api/documents",
        headers={"X-User-Id": "alice"},
        files={"file": ("status.txt", content.encode(), "text/plain")},
    )
    assert response.status_code == 201
    return response.json()


def process_jobs(dsn: str) -> None:
    async def process() -> None:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await process_all_embedding_jobs(conn, FakeProvider())

    asyncio.run(process())


def test_status_is_available_without_authentication_and_reports_operational_fields(
    db_client: TestClient,
):
    response = db_client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "node_address": body["node_address"],
        "node_port": body["node_port"],
        "jobs": {"pending": 0, "processing": 0, "error": 0},
        "inconsistent_documents": 0,
        "embedding_provider": "fake",
        "reconnect_events": None,
    }
    assert body["node_address"] is None or isinstance(body["node_address"], str)
    assert isinstance(body["node_port"], int)


def test_status_reports_pending_jobs_until_the_worker_processes_them(
    db_client: TestClient, migrated_db: str
):
    upload(db_client)

    assert db_client.get("/api/system/status").json()["jobs"] == {
        "pending": 1,
        "processing": 0,
        "error": 0,
    }

    process_jobs(migrated_db)

    assert db_client.get("/api/system/status").json()["jobs"] == {
        "pending": 0,
        "processing": 0,
        "error": 0,
    }


def test_status_observes_version_drift_and_convergence(
    db_client: TestClient, migrated_db: str
):
    document = upload(db_client)
    process_jobs(migrated_db)
    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 0

    response = db_client.put(
        f"/api/documents/{document['id']}",
        headers={"X-User-Id": "alice"},
        json={"content": "수정된 추출 텍스트", "version": document["version"]},
    )
    assert response.status_code == 200
    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 1

    process_jobs(migrated_db)

    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 0


def test_consistency_counter_counts_documents_not_chunks(
    db_client: TestClient, migrated_db: str
):
    document = upload(db_client, "OpenSQL 정합성 문단.\n\n" * 300)
    process_jobs(migrated_db)
    detail = db_client.get(f"/api/documents/{document['id']}").json()
    assert detail["chunk_count"] >= 2

    response = db_client.put(
        f"/api/documents/{document['id']}",
        headers={"X-User-Id": "alice"},
        json={"content": "새 추출 텍스트", "version": document["version"]},
    )
    assert response.status_code == 200

    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 1
