import psycopg
import pytest
from conftest import insert_test_document, login_as
from fastapi.testclient import TestClient


def _insert_document(dsn: str, **kwargs) -> str:
    async def insert() -> str:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            return str(await insert_test_document(conn, **kwargs))

    import asyncio

    return asyncio.run(insert())


def _insert_edge(
    dsn: str, source_id: str, target_id: str, *, kind: str = "related", score: float = 0.8
) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO document_edges
                (src_document_id, dst_document_id, kind, score)
            VALUES (%s, %s, %s, %s)
            """,
            (source_id, target_id, kind, score),
        )


@pytest.fixture(autouse=True)
def logged_in(db_client: TestClient):
    """진단 조회도 로그인을 요구한다 (ADR-028). 익명 경계는 아래에서 따로 단언한다."""
    login_as(db_client, "alice")


def test_diagnostics_require_login(db_client: TestClient):
    db_client.post("/api/auth/logout")

    assert db_client.get("/api/diagnostics").status_code == 401


def test_diagnostics_lists_only_documents_without_visible_edges(
    db_client: TestClient, migrated_db: str
):
    orphan_id = _insert_document(
        migrated_db, title="고아 문서", content="연결 없는 문서", tags=["정리됨"]
    )
    connected_id = _insert_document(
        migrated_db, title="연결 문서", content="관계가 있는 문서", tags=["정리됨"]
    )
    neighbor_id = _insert_document(
        migrated_db, title="이웃 문서", content="연결된 이웃", tags=["정리됨"]
    )
    _insert_edge(migrated_db, connected_id, neighbor_id)

    response = db_client.get("/api/diagnostics")

    assert response.status_code == 200
    orphan_ids = {item["document_id"] for item in response.json()["orphans"]["items"]}
    assert orphan_id in orphan_ids
    assert connected_id not in orphan_ids
    assert neighbor_id not in orphan_ids


def test_diagnostics_lists_identical_content_separately_from_high_overlap(
    db_client: TestClient, migrated_db: str
):
    exact_a = _insert_document(
        migrated_db, title="동일 A", content="완전히 같은 추출 텍스트", tags=["분류"]
    )
    exact_b = _insert_document(
        migrated_db, title="동일 B", content="완전히 같은 추출 텍스트", tags=["분류"]
    )
    overlap_a = _insert_document(
        migrated_db, title="겹침 A", content="대부분 겹치는 첫 문서", tags=["분류"]
    )
    overlap_b = _insert_document(
        migrated_db, title="겹침 B", content="대부분 겹치는 둘째 문서", tags=["분류"]
    )
    _insert_edge(migrated_db, exact_a, exact_b, kind="overlaps", score=1.0)
    _insert_edge(migrated_db, overlap_a, overlap_b, kind="overlaps", score=0.97)

    duplicates = db_client.get("/api/diagnostics").json()["duplicates"]

    assert duplicates["identical"]["count"] == 1
    assert duplicates["identical"]["items"][0]["score"] is None
    assert duplicates["overlaps"]["count"] == 1
    assert duplicates["overlaps"]["items"][0]["score"] == pytest.approx(0.97)


def test_diagnostics_uses_only_high_overlap_scores_for_duplicate_candidates(
    db_client: TestClient, migrated_db: str
):
    high_a = _insert_document(migrated_db, title="높음 A", content="높음 A", tags=["분류"])
    high_b = _insert_document(migrated_db, title="높음 B", content="높음 B", tags=["분류"])
    low_a = _insert_document(migrated_db, title="낮음 A", content="낮음 A", tags=["분류"])
    low_b = _insert_document(migrated_db, title="낮음 B", content="낮음 B", tags=["분류"])
    _insert_edge(migrated_db, high_a, high_b, kind="overlaps", score=0.95)
    _insert_edge(migrated_db, low_a, low_b, kind="overlaps", score=0.94)

    items = db_client.get("/api/diagnostics").json()["duplicates"]["overlaps"]["items"]
    ids = {item["first"]["document_id"] for item in items} | {
        item["second"]["document_id"] for item in items
    }

    assert {high_a, high_b} <= ids
    assert low_a not in ids
    assert low_b not in ids


def test_diagnostics_lists_only_documents_without_tags(
    db_client: TestClient, migrated_db: str
):
    untagged_id = _insert_document(
        migrated_db, title="미분류", content="태그가 없는 문서"
    )
    tagged_id = _insert_document(
        migrated_db, title="분류됨", content="태그가 있는 문서", tags=["분류"]
    )

    body = db_client.get("/api/diagnostics").json()["uncategorized"]
    ids = {item["document_id"] for item in body["items"]}

    assert untagged_id in ids
    assert tagged_id not in ids


def test_diagnostics_lists_broken_links_without_exposing_a_reason(
    db_client: TestClient, migrated_db: str
):
    source_id = _insert_document(
        migrated_db, title="깨진 링크 출발", content="[[없는 문서]]", tags=["분류"]
    )

    response = db_client.get("/api/diagnostics")

    assert response.status_code == 200
    broken = response.json()["broken_links"]
    assert broken == {
        "count": 1,
        "items": [
            {
                "source": {"document_id": source_id, "title": "깨진 링크 출발"},
                "target_title": "없는 문서",
            }
        ],
    }
    assert "reason" not in response.text


def test_document_connected_only_to_invisible_private_document_looks_orphaned(
    db_client: TestClient, migrated_db: str
):
    public_id = _insert_document(
        migrated_db, title="공개 문서", content="비공개와만 연결", tags=["분류"]
    )
    private_id = _insert_document(
        migrated_db,
        title="앨리스 비공개",
        content="숨겨진 이웃",
        owner_id="alice",
        visibility="private",
        tags=["분류"],
    )
    _insert_edge(migrated_db, public_id, private_id)

    # 익명이 막힌 뒤에도(ADR-028) "이웃을 볼 수 없는 시선에게는 고아로 보인다"는 그대로
    # 검증 대상이다. 익명 대신 그 비공개 문서를 볼 수 없는 다른 계정으로 본다.
    db_client.post("/api/auth/logout")
    login_as(db_client, "bob")
    other = db_client.get("/api/diagnostics").json()
    other_orphans = {item["document_id"] for item in other["orphans"]["items"]}
    db_client.post("/api/auth/logout")
    login_as(db_client, "alice")
    owner = db_client.get("/api/diagnostics").json()
    owner_orphans = {item["document_id"] for item in owner["orphans"]["items"]}

    assert public_id in other_orphans  # 고아 판정도 열람 범위 안에서만 집계한다.
    assert private_id not in other_orphans
    assert public_id not in owner_orphans


def test_diagnostics_limits_duplicate_pair_lists_but_keeps_the_full_count(
    db_client: TestClient, migrated_db: str
):
    for index in range(6):
        _insert_document(
            migrated_db,
            title=f"동일 문서 {index}",
            content="목록 상한을 확인하는 동일 텍스트",
            tags=["분류"],
        )

    identical = db_client.get("/api/diagnostics").json()["duplicates"]["identical"]

    assert identical["count"] == 15
    assert len(identical["items"]) == 10
