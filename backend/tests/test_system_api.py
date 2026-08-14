import psycopg
import pytest
from conftest import login_as, run_embedding_worker, upload_document
from fastapi.testclient import TestClient


def upload(client: TestClient, content: str = "OpenSQL status") -> dict:
    response = upload_document(client, filename="status.txt", content=content.encode())
    assert response.status_code == 201
    return response.json()


@pytest.fixture(autouse=True)
def logged_in(db_client: TestClient):
    """시스템 상태 조회도 로그인을 요구한다 (ADR-028). 익명 경계는 아래에서 따로 단언한다."""
    login_as(db_client, "alice")


def test_status_requires_login(db_client: TestClient):
    db_client.post("/api/auth/logout")

    assert db_client.get("/api/system/status").status_code == 401


def test_status_reports_operational_fields(db_client: TestClient):
    response = db_client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "node_address",
        "node_port",
        "jobs",
        "zombie_timeout_minutes",
        "last_job_finished_at",
        "inconsistent_documents",
        "embedding_provider",
    }
    assert "reconnect_events" not in body
    assert body["jobs"] == {
        "pending": 0,
        "processing": 0,
        "recovery_pending": 0,
        "error": 0,
    }
    assert body["zombie_timeout_minutes"] == 5
    assert body["last_job_finished_at"] is None
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
        "recovery_pending": 0,
        "error": 0,
    }

    run_embedding_worker(migrated_db)

    completed = db_client.get("/api/system/status").json()
    assert completed["jobs"] == {
        "pending": 0,
        "processing": 0,
        "recovery_pending": 0,
        "error": 0,
    }
    assert completed["last_job_finished_at"] is not None


def test_status_distinguishes_fresh_processing_jobs_from_recovery_pending_jobs(
    db_client: TestClient, migrated_db: str
):
    upload(db_client, "fresh processing")
    upload(db_client, "stale processing")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            """
            UPDATE embedding_jobs
               SET status = 'processing', started_at = now()
             WHERE id = (SELECT min(id) FROM embedding_jobs)
            """
        )
        conn.execute(
            """
            UPDATE embedding_jobs
               SET status = 'processing', started_at = now() - interval '10 minutes'
             WHERE id = (SELECT max(id) FROM embedding_jobs)
            """
        )

    body = db_client.get("/api/system/status").json()

    assert body["jobs"] == {
        "pending": 0,
        "processing": 2,
        "recovery_pending": 1,
        "error": 0,
    }
    assert body["zombie_timeout_minutes"] == 5


def test_status_observes_version_drift_and_convergence(
    db_client: TestClient, migrated_db: str
):
    document = upload(db_client)
    run_embedding_worker(migrated_db)
    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 0

    login_as(db_client, "alice")
    response = db_client.put(
        f"/api/documents/{document['id']}",
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

    login_as(db_client, "alice")
    response = db_client.put(
        f"/api/documents/{document['id']}",
        json={"content": "새 추출 텍스트", "version": document["version"]},
    )
    assert response.status_code == 200

    assert db_client.get("/api/system/status").json()["inconsistent_documents"] == 1
