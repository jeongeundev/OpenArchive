from conftest import run_embedding_worker, upload_document
from fastapi.testclient import TestClient


def upload(client: TestClient, content: str = "OpenSQL status") -> dict:
    response = upload_document(client, filename="status.txt", content=content.encode())
    assert response.status_code == 201
    return response.json()


def test_status_is_available_without_authentication_and_reports_operational_fields(
    db_client: TestClient,
):
    response = db_client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "node_address",
        "node_port",
        "jobs",
        "inconsistent_documents",
        "embedding_provider",
    }
    removed_field_parts = ("reconnect", "events")
    removed_field = "_".join(removed_field_parts)
    assert removed_field not in body
    assert body["jobs"] == {"pending": 0, "processing": 0, "error": 0}
    assert body["inconsistent_documents"] == 0
    assert body["embedding_provider"] == "fake"
    # 접속 노드는 환경마다 다르다. TCP면 주소가, 유닉스 소켓이면 NULL이 온다.
    # 값 자체가 아니라 페일오버 데모가 읽을 수 있는 형태인지를 본다.
    assert body["node_address"] is None or isinstance(body["node_address"], str)
    assert isinstance(body["node_port"], int) and body["node_port"] > 0


def test_status_reports_pending_jobs_until_the_worker_processes_them(
    db_client: TestClient, migrated_db: str
):
    upload(db_client)

    assert db_client.get("/api/system/status").json()["jobs"] == {
        "pending": 1,
        "processing": 0,
        "error": 0,
    }

    run_embedding_worker(migrated_db)

    assert db_client.get("/api/system/status").json()["jobs"] == {
        "pending": 0,
        "processing": 0,
        "error": 0,
    }


def test_status_observes_version_drift_and_convergence(
    db_client: TestClient, migrated_db: str
):
    document = upload(db_client)
    run_embedding_worker(migrated_db)
    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 0

    response = db_client.put(
        f"/api/documents/{document['id']}",
        headers={"X-User-Id": "alice"},
        json={"content": "수정된 추출 텍스트", "version": document["version"]},
    )
    assert response.status_code == 200
    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 1

    run_embedding_worker(migrated_db)

    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 0


def test_consistency_counter_counts_documents_not_chunks(
    db_client: TestClient, migrated_db: str
):
    document = upload(db_client, "OpenSQL 정합성 문단.\n\n" * 300)
    run_embedding_worker(migrated_db)
    detail = db_client.get(f"/api/documents/{document['id']}").json()
    assert detail["chunk_count"] >= 2

    response = db_client.put(
        f"/api/documents/{document['id']}",
        headers={"X-User-Id": "alice"},
        json={"content": "새 추출 텍스트", "version": document["version"]},
    )
    assert response.status_code == 200

    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 1
