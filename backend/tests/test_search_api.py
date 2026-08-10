import asyncio

import psycopg
import pytest
from conftest import insert_test_document, process_all_embedding_jobs
from fastapi.testclient import TestClient

from app.embeddings.fake import FakeProvider
from app.main import app
from app.services.search import MAX_K, SEARCH_SQL


def seed_documents(dsn: str, documents: list[dict], *, process: bool = True) -> list[str]:
    async def seed() -> list[str]:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            ids = [str(await insert_test_document(conn, **document)) for document in documents]
            if process:
                await process_all_embedding_jobs(conn, FakeProvider())
            return ids

    return asyncio.run(seed())


def test_search_returns_a_matching_document(db_client: TestClient, migrated_db: str):
    """검증 대상은 API가 서비스 결과를 그대로 전달하는가이지 검색 품질이 아니다."""
    matching_id, _ = seed_documents(
        migrated_db,
        [
            {"title": "정합성 규정", "content": "OpenSQL 정합성 트리거 운영 규정"},
            {"title": "휴가 안내", "content": "연차 휴가 신청 승인 안내"},
        ],
    )

    response = db_client.post("/api/search", json={"query": "OpenSQL 정합성"})

    assert response.status_code == 200
    assert response.json()["items"][0]["document_id"] == matching_id


def test_search_response_exposes_the_relation_it_arrived_through(
    db_client: TestClient, migrated_db: str
):
    """step8 AC 9번이 GET/POST 착오로 검증하지 못한 자리 — via가 응답까지 오는가.

    edge는 손으로 만들지 않는다. 적재 → 임베딩 완료 → 트리거 → 검색 확장 → 직렬화가
    한 줄로 이어지는지를 API 경계에서 본다.
    """
    entry_id, neighbor_id = seed_documents(
        migrated_db,
        [
            {"title": "직접 진입점", "content": "정합성 직접 일치 문장 " * 900},
            {"title": "관계로만 도달", "content": "질의 어휘가 전혀 없는 별도 문서"},
        ],
    )

    body = db_client.post(
        "/api/search", json={"query": "정합성 직접 일치 문장", "k": 2}
    ).json()

    items = body["items"]
    assert items[0]["document_id"] == entry_id
    assert items[0]["via"] is None
    expanded = [item for item in items if item["via"] is not None]
    assert [item["document_id"] for item in expanded] == [neighbor_id]
    assert expanded[0]["via"]["from_document_id"] == entry_id
    assert expanded[0]["via"]["kind"] in {"overlaps", "related"}
    assert expanded[0]["via"]["depth"] == 1


def test_search_exposes_the_service_sql_without_bound_vector(
    db_client: TestClient, migrated_db: str
):
    seed_documents(migrated_db, [{"title": "규정", "content": "임베딩 잡 생성 규정"}])

    body = db_client.post("/api/search", json={"query": "임베딩 잡 생성"}).json()

    assert body["sql"] == SEARCH_SQL
    assert "%(qvec)s" in body["sql"]
    assert "[0.0," not in body["sql"]


@pytest.mark.parametrize("query", ["", " \t\n"])
def test_search_rejects_a_blank_query(db_client: TestClient, query: str):
    response = db_client.post("/api/search", json={"query": query})

    assert response.status_code == 400


@pytest.mark.parametrize("k", [0, MAX_K + 1])
def test_search_rejects_k_outside_the_supported_range(db_client: TestClient, k: int):
    response = db_client.post("/api/search", json={"query": "질의", "k": k})

    assert response.status_code == 422


def test_search_accepts_max_k(db_client: TestClient, migrated_db: str):
    seed_documents(migrated_db, [{"title": "문서", "content": "최대 검색 건수"}])

    response = db_client.post("/api/search", json={"query": "최대 검색 건수", "k": MAX_K})

    assert response.status_code == 200


def test_search_defaults_to_at_most_ten_items(db_client: TestClient, migrated_db: str):
    seed_documents(
        migrated_db,
        [{"title": f"문서 {index}", "content": f"공통 검색어 고유{index}"} for index in range(12)],
    )

    response = db_client.post("/api/search", json={"query": "공통 검색어"})

    assert len(response.json()["items"]) == 10


def test_search_applies_anonymous_and_owner_visibility(db_client: TestClient, migrated_db: str):
    public_id, private_id = seed_documents(
        migrated_db,
        [
            {"title": "공개", "content": "접근통제 규정"},
            {
                "title": "비공개",
                "content": "접근통제 기밀 규정",
                "visibility": "private",
                "owner_id": "alice",
            },
        ],
    )

    anonymous = db_client.post("/api/search", json={"query": "접근통제"}).json()["items"]
    owner = db_client.post(
        "/api/search", headers={"X-User-Id": "alice"}, json={"query": "접근통제"}
    ).json()["items"]

    assert {item["document_id"] for item in anonymous} == {public_id}
    assert {item["document_id"] for item in owner} == {public_id, private_id}


def test_search_applies_tag_and_content_type_filters(db_client: TestClient, migrated_db: str):
    expected, _, _ = seed_documents(
        migrated_db,
        [
            {
                "title": "PDF 규정",
                "content": "보안 점검 규정",
                "tags": ["규정"],
                "content_type": "pdf",
            },
            {"title": "MD 규정", "content": "보안 점검 규정", "tags": ["규정"]},
            {"title": "PDF 안내", "content": "보안 점검 안내", "tags": ["안내"], "content_type": "pdf"},
        ],
    )

    items = db_client.post(
        "/api/search",
        json={"query": "보안 점검", "tags": ["규정"], "content_type": "pdf"},
    ).json()["items"]

    assert [item["document_id"] for item in items] == [expected]


def test_search_treats_an_empty_tag_list_as_no_filter(db_client: TestClient, migrated_db: str):
    """빈 배열은 "태그를 고르지 않았다"는 뜻이지 필터가 아니다.

    필터로 넘기면 `d.tags && '{}'`가 어느 행에서도 참이 되지 않아, 에러 없이 결과가
    통째로 사라진다. 화면에서 태그 칸을 비우고 검색하는 것이 기본 경로다.
    """
    matching_id, _ = seed_documents(
        migrated_db,
        [
            {"title": "정합성 규정", "content": "OpenSQL 정합성 트리거 운영 규정", "tags": ["규정"]},
            {"title": "휴가 안내", "content": "연차 휴가 신청 승인 안내"},
        ],
    )

    items = db_client.post("/api/search", json={"query": "OpenSQL 정합성", "tags": []}).json()[
        "items"
    ]

    assert items[0]["document_id"] == matching_id


def test_search_combines_structured_filter_with_vector_ranking(
    db_client: TestClient, migrated_db: str
):
    """검증 대상은 API가 서비스 결과를 그대로 전달하는가이지 검색 품질이 아니다."""
    both_id, unrelated_id, _ = seed_documents(
        migrated_db,
        [
            {"title": "정합성 규정", "content": "OpenSQL 정합성 운영", "tags": ["규정"]},
            {"title": "복지 규정", "content": "식대 휴가 복지", "tags": ["규정"]},
            {"title": "태그 불일치", "content": "OpenSQL 정합성 운영", "tags": ["안내"]},
        ],
    )

    items = db_client.post(
        "/api/search", json={"query": "OpenSQL 정합성", "tags": ["규정"]}
    ).json()["items"]

    assert items[0]["document_id"] == both_id
    scores = {item["document_id"]: item["score"] for item in items}
    assert scores[both_id] > scores[unrelated_id]


def test_search_returns_one_item_per_document(db_client: TestClient, migrated_db: str):
    long_id, _ = seed_documents(
        migrated_db,
        [
            {"title": "긴 문서", "content": "OpenSQL 정합성 " * 900},
            {"title": "짧은 문서", "content": "OpenSQL 정합성 안내"},
        ],
    )

    items = db_client.post("/api/search", json={"query": "OpenSQL 정합성"}).json()["items"]

    assert [item["document_id"] for item in items].count(long_id) == 1


def test_search_omits_a_document_without_chunks(db_client: TestClient, migrated_db: str):
    (document_id,) = seed_documents(
        migrated_db, [{"title": "대기", "content": "OpenSQL 정합성 대기"}], process=False
    )

    items = db_client.post("/api/search", json={"query": "OpenSQL 정합성"}).json()["items"]

    assert document_id not in {item["document_id"] for item in items}


def test_search_reuses_the_lifespan_provider(db_client: TestClient):
    provider = app.state.provider

    assert db_client.post("/api/search", json={"query": "첫 검색"}).status_code == 200
    assert db_client.post("/api/search", json={"query": "둘째 검색"}).status_code == 200
    assert app.state.provider is provider
