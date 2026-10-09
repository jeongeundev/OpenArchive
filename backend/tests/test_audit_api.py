"""관리자 감사 로그 조회 API (ADR-055 결정 7·8)."""

import psycopg
import pytest
from conftest import login_as
from fastapi.testclient import TestClient
from test_groups_api import issue_admin_token, login_admin
from test_token_access import bearer

ENTRY_KEYS = {
    "id",
    "occurred_at",
    "action",
    "actor",
    "actor_via",
    "db_role",
    "document_id",
    "document_title",
    "detail",
}


def create_text(client: TestClient, username: str, title: str, **extra) -> str:
    login_as(client, username)
    response = client.post("/api/documents/text", json={"title": title, "content": "글", **extra})
    assert response.status_code == 201
    return response.json()["id"]


def audit_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]


def seed(client: TestClient, dsn: str) -> None:
    """alice 문서 둘(하나는 삭제), bob 문서 하나."""
    first = create_text(client, "alice", "첫째")
    create_text(client, "alice", "둘째")
    assert client.delete(f"/api/documents/{first}?permanent=true").status_code == 204
    create_text(client, "bob", "셋째")
    client.cookies.clear()
    login_admin(client, dsn)


def test_admin_lists_entries_newest_first(db_client: TestClient, migrated_db: str):
    seed(db_client, migrated_db)

    response = db_client.get("/api/admin/audit")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 7
    assert [item["id"] for item in items] == sorted((item["id"] for item in items), reverse=True)
    assert all(set(item) == ENTRY_KEYS for item in items)
    assert [(item["action"], item["actor"], item["document_title"]) for item in items] == [
        ("user_changed", None, None),
        ("document_created", "bob", "셋째"),
        ("user_changed", None, None),
        ("document_deleted", "alice", "첫째"),
        ("document_created", "alice", "둘째"),
        ("document_created", "alice", "첫째"),
        ("user_changed", None, None),
    ]
    assert items[1]["actor_via"] == "session"
    assert items[1]["detail"] == {}
    assert [item["detail"] for item in items if item["action"] == "user_changed"] == [
        {"change": "created", "user": name} for name in ("boss", "bob", "alice")
    ]
    assert response.json()["next_before_id"] is None


def test_filters_by_actor_and_action(db_client: TestClient, migrated_db: str):
    seed(db_client, migrated_db)

    by_actor = db_client.get("/api/admin/audit", params={"actor": "alice"}).json()["items"]
    by_action = db_client.get(
        "/api/admin/audit", params={"action": "document_deleted"}
    ).json()["items"]
    both = db_client.get(
        "/api/admin/audit", params={"actor": "bob", "action": "document_created"}
    ).json()["items"]
    none = db_client.get(
        "/api/admin/audit", params={"actor": "bob", "action": "document_deleted"}
    ).json()["items"]

    assert {item["actor"] for item in by_actor} == {"alice"} and len(by_actor) == 3
    assert [item["document_title"] for item in by_action] == ["첫째"]
    assert [item["document_title"] for item in both] == ["셋째"]
    assert none == []


def test_unknown_action_is_422(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)

    assert db_client.get("/api/admin/audit", params={"action": "모르는값"}).status_code == 422


@pytest.mark.parametrize("limit", [0, 201])
def test_limit_out_of_range_is_422(db_client: TestClient, migrated_db: str, limit: int):
    login_admin(db_client, migrated_db)

    assert db_client.get("/api/admin/audit", params={"limit": limit}).status_code == 422


def test_cursor_pages_without_gaps_or_duplicates(db_client: TestClient, migrated_db: str):
    seed(db_client, migrated_db)
    all_ids = [item["id"] for item in db_client.get("/api/admin/audit").json()["items"]]

    first = db_client.get("/api/admin/audit", params={"limit": 4}).json()
    second = db_client.get(
        "/api/admin/audit", params={"limit": 4, "before_id": first["next_before_id"]}
    ).json()
    third = db_client.get(
        "/api/admin/audit", params={"limit": 4, "before_id": all_ids[-1]}
    ).json()

    assert first["next_before_id"] == first["items"][-1]["id"]
    assert [item["id"] for item in first["items"] + second["items"]] == all_ids
    assert second["next_before_id"] is None
    assert third == {"items": [], "next_before_id": None}


def test_last_partial_page_has_no_cursor(db_client: TestClient, migrated_db: str):
    seed(db_client, migrated_db)

    page = db_client.get("/api/admin/audit", params={"limit": 4}).json()
    rest = db_client.get(
        "/api/admin/audit", params={"limit": 4, "before_id": page["next_before_id"]}
    ).json()

    assert len(rest["items"]) == 3
    assert rest["next_before_id"] is None


def test_regular_user_is_403(db_client: TestClient):
    login_as(db_client, "alice")

    response = db_client.get("/api/admin/audit")

    assert response.status_code == 403
    assert response.json()["detail"] == "관리자 권한이 필요합니다."


def test_anonymous_is_401(db_client: TestClient):
    assert db_client.get("/api/admin/audit").status_code == 401


def test_admin_token_is_rejected(db_client: TestClient, migrated_db: str):
    login_admin(db_client, migrated_db)
    token = issue_admin_token(migrated_db)
    db_client.cookies.clear()

    response = db_client.get("/api/admin/audit", headers=bearer(token))

    assert response.status_code == 403


def test_admin_sees_title_but_not_body_of_hidden_document(
    db_client: TestClient, migrated_db: str
):
    secret = "본문에만-있는-고유-문자열-7f3a"
    login_as(db_client, "alice")
    response = db_client.post(
        "/api/documents/text",
        json={"title": "인사 평가", "content": secret, "visibility": "private"},
    )
    assert response.status_code == 201
    document_id = response.json()["id"]
    db_client.cookies.clear()
    login_admin(db_client, migrated_db)
    assert db_client.get(f"/api/documents/{document_id}").status_code == 404

    response = db_client.get("/api/admin/audit")

    assert response.status_code == 200
    items = [item for item in response.json()["items"] if item["document_id"] == document_id]
    assert [item["document_title"] for item in items] == ["인사 평가"]
    assert secret not in response.text


def test_listing_does_not_record(db_client: TestClient, migrated_db: str):
    seed(db_client, migrated_db)
    before = audit_count(migrated_db)

    assert db_client.get("/api/admin/audit").status_code == 200
    assert db_client.get("/api/admin/audit", params={"actor": "alice"}).status_code == 200

    assert audit_count(migrated_db) == before


def test_csv_exports_all_filtered_rows_without_recording(db_client, migrated_db):
    import csv
    import io
    import re

    login_as(db_client, "alice")
    for i in range(55):
        response = db_client.post(
            "/api/documents/text", json={"title": f"CSV {i}", "content": "글"}
        )
        assert response.status_code == 201
    create_text(db_client, "bob", "제외")
    login_admin(db_client, migrated_db)
    before = audit_count(migrated_db)
    response = db_client.get("/api/admin/audit", params={
        "format": "csv", "actor": "alice", "action": "document_created",
        "limit": 1, "before_id": 1,
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(r'attachment; filename="audit-\d{8}-\d{6}\.csv"',
                        response.headers["content-disposition"])
    assert response.text.startswith("\ufeff")
    reader = csv.DictReader(io.StringIO(response.text[1:]))
    assert reader.fieldnames == [
        "id", "occurred_at", "actor", "actor_via", "db_role", "action",
        "document_id", "document_title", "detail",
    ]
    rows = list(reader)
    assert len(rows) == 55
    assert {row["actor"] for row in rows} == {"alice"}
    assert {row["action"] for row in rows} == {"document_created"}
    assert [int(row["id"]) for row in rows] == sorted(
        (int(row["id"]) for row in rows), reverse=True
    )
    assert audit_count(migrated_db) == before


def test_csv_auth_and_invalid_queries(db_client, migrated_db):
    assert db_client.get("/api/admin/audit?format=csv").status_code == 401
    login_as(db_client, "alice")
    assert db_client.get("/api/admin/audit?format=csv").status_code == 403
    login_admin(db_client, migrated_db)
    assert db_client.get("/api/admin/audit?format=unknown").status_code == 422
    assert db_client.get("/api/admin/audit?format=csv&action=unknown").status_code == 422
    token = issue_admin_token(migrated_db)
    db_client.cookies.clear()
    assert db_client.get("/api/admin/audit?format=csv", headers=bearer(token)).status_code == 403
