import asyncio

import psycopg
import pytest
from conftest import insert_test_document, login_as
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def logged_in(db_client: TestClient):
    """덩어리 조회도 로그인을 요구한다 (ADR-028). 익명 경계는 아래에서 따로 단언한다."""
    login_as(db_client, "alice")


def test_clusters_require_login(db_client: TestClient):
    db_client.post("/api/auth/logout")

    assert db_client.get("/api/clusters").status_code == 401


def _insert_document(dsn: str, **kwargs) -> str:
    async def insert() -> str:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            return str(await insert_test_document(conn, **kwargs))

    return asyncio.run(insert())


def _insert_edge(dsn: str, source_id: str, target_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO document_edges
                (src_document_id, dst_document_id, kind, score)
            VALUES (%s, %s, 'related', 0.8)
            """,
            (source_id, target_id),
        )


def _insert_bidirectional_edge(dsn: str, first_id: str, second_id: str) -> None:
    _insert_edge(dsn, first_id, second_id)
    _insert_edge(dsn, second_id, first_id)


def test_clusters_returns_topic_sizes_connections_and_documents(
    db_client: TestClient, migrated_db: str
):
    search_a = _insert_document(
        migrated_db, title="검색 A", content="검색 A", tags=["검색", "공통"]
    )
    _insert_document(
        migrated_db, title="검색 B", content="검색 B", tags=["검색"]
    )
    database = _insert_document(
        migrated_db, title="DB", content="DB", tags=["데이터베이스"]
    )
    _insert_bidirectional_edge(migrated_db, search_a, database)

    response = db_client.get("/api/clusters")

    assert response.status_code == 200
    body = response.json()
    clusters = {cluster["name"]: cluster for cluster in body["clusters"]}
    assert clusters["검색"]["size"] == 2
    assert {item["title"] for item in clusters["검색"]["documents"]} == {
        "검색 A",
        "검색 B",
    }
    assert clusters["데이터베이스"]["size"] == 1
    assert body["connections"] == [
        {"source": "검색", "target": "데이터베이스", "count": 1}
    ]


def test_untagged_documents_are_assigned_to_uncategorized(
    db_client: TestClient, migrated_db: str
):
    document_id = _insert_document(
        migrated_db, title="태그 없음", content="분류되지 않은 문서"
    )

    clusters = db_client.get("/api/clusters").json()["clusters"]
    uncategorized = next(cluster for cluster in clusters if cluster["name"] == "미분류")

    assert uncategorized["size"] == 1
    assert uncategorized["documents"] == [
        {"document_id": document_id, "title": "태그 없음"}
    ]


def test_cluster_count_is_capped_and_small_topics_are_merged_into_other(
    db_client: TestClient, migrated_db: str
):
    for index in range(22):
        _insert_document(
            migrated_db,
            title=f"주제 {index:02d}",
            content=f"서로 다른 내용 {index}",
            tags=[f"태그-{index:02d}"],
        )

    clusters = db_client.get("/api/clusters").json()["clusters"]

    assert len(clusters) == 20
    assert sum(cluster["size"] for cluster in clusters) == 22
    assert next(cluster for cluster in clusters if cluster["name"] == "기타")["size"] == 3


def test_reserved_bucket_names_do_not_merge_with_same_named_tags(
    db_client: TestClient, migrated_db: str
):
    _insert_document(migrated_db, title="태그 없음", content="태그 없음")
    _insert_document(
        migrated_db, title="명시적 미분류", content="명시적 미분류", tags=["미분류"]
    )

    clusters = {item["name"]: item for item in db_client.get("/api/clusters").json()["clusters"]}

    assert clusters["미분류"]["size"] == 1
    assert clusters["미분류 (태그)"]["size"] == 1
