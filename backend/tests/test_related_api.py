import pytest
from conftest import login_as, run_embedding_worker, upload_document
from fastapi.testclient import TestClient

from app.services.search import MAX_K


def upload(
    client: TestClient,
    *,
    filename: str,
    content: str,
    user_id: str = "alice",
    tags: str | None = None,
    visibility: str = "public",
) -> str:
    data = {"visibility": visibility}
    if tags is not None:
        data["tags"] = tags
    response = upload_document(
        client,
        filename=filename,
        content=content.encode(),
        user_id=user_id,
        data=data,
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_related_returns_ranked_items_and_chunk_version(
    db_client: TestClient, migrated_db: str
):
    source_id = upload(
        db_client,
        filename="source.txt",
        content="OpenSQL 정합성 트리거 운영",
    )
    closest_id = upload(
        db_client,
        filename="closest.txt",
        content="OpenSQL 정합성 트리거 안내",
    )
    upload(db_client, filename="other.txt", content="휴가 식대 복지 안내")
    run_embedding_worker(migrated_db)

    response = db_client.get(f"/api/documents/{source_id}/related")

    assert response.status_code == 200
    body = response.json()
    assert closest_id in {item["document_id"] for item in body["items"]}
    assert source_id not in {item["document_id"] for item in body["items"]}
    # score의 척도가 kind마다 다르므로(overlaps는 매칭 비율, related는 1-최소거리)
    # 전체 정렬은 성립하지 않는다. 같은 kind 안에서만 내림차순이고, kind는 섞이지 않고
    # 붙어 나와야 한다 (ADR-029). kind가 CHECK 제약으로 이미 보장되는 값인지가 아니라
    # 이 순서가 응답까지 살아 오는지를 단언한다.
    kinds = [item["kind"] for item in body["items"]]
    assert kinds == sorted(kinds, key=kinds.index)
    for kind in set(kinds):
        scores = [item["score"] for item in body["items"] if item["kind"] == kind]
        assert scores == sorted(scores, reverse=True)
    assert body["based_on_version"] == 1
    assert body["reason"] is None


def test_related_without_chunks_returns_not_indexed_and_identical_documents(
    db_client: TestClient,
):
    content = "아직 색인되지 않은 동일 텍스트"
    source_id = upload(db_client, filename="source.txt", content=content)
    identical_id = upload(db_client, filename="copy.txt", content=content)

    response = db_client.get(f"/api/documents/{source_id}/related")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "identical": [{"document_id": identical_id, "title": "copy"}],
        "based_on_version": None,
        "reason": "not_indexed",
    }


@pytest.mark.parametrize("path", ["related", "tag-suggestions"])
def test_related_endpoints_hide_another_users_private_source(
    db_client: TestClient, path: str
):
    private_id = upload(
        db_client,
        filename="private.txt",
        content="비공개 문서",
        visibility="private",
    )

    login_as(db_client, "bob")
    response = db_client.get(f"/api/documents/{private_id}/{path}")

    assert response.status_code == 404


def test_related_does_not_leak_another_users_private_candidates(
    db_client: TestClient, migrated_db: str
):
    content = "기밀 접근통제 운영"
    source_id = upload(db_client, filename="source.txt", content=content)
    public_id = upload(db_client, filename="public.txt", content=content)
    private_id = upload(
        db_client,
        filename="private.txt",
        content=content,
        user_id="bob",
        visibility="private",
    )
    run_embedding_worker(migrated_db)

    db_client.post("/api/auth/logout")
    anonymous = db_client.get(
        f"/api/documents/{source_id}/related", headers={"X-User-Id": "alice"}
    ).json()
    assert private_id not in {
        item["document_id"] for item in anonymous["items"] + anonymous["identical"]
    }

    login_as(db_client, "alice")
    body = db_client.get(f"/api/documents/{source_id}/related").json()

    assert {item["document_id"] for item in body["items"]} == {public_id}
    assert {item["document_id"] for item in body["identical"]} == {public_id}
    assert private_id not in {
        item["document_id"] for item in body["items"] + body["identical"]
    }


def test_tag_suggestions_return_frequency_order_and_exclude_existing_tags(
    db_client: TestClient, migrated_db: str
):
    source_id = upload(
        db_client,
        filename="source.txt",
        content="OpenSQL 정합성 트리거 운영",
        tags="opensql",
    )
    upload(
        db_client,
        filename="first.txt",
        content="OpenSQL 정합성 트리거 안내",
        tags="database",
    )
    upload(
        db_client,
        filename="second.txt",
        content="OpenSQL 정합성 운영 안내",
        tags="database",
    )
    upload(
        db_client,
        filename="third.txt",
        content="OpenSQL 트리거 운영 지침",
        tags="opensql",
    )
    upload(
        db_client,
        filename="fourth.txt",
        content="OpenSQL 트리거 관리 지침",
        tags="worker",
    )
    run_embedding_worker(migrated_db)

    response = db_client.get(f"/api/documents/{source_id}/tag-suggestions")

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"tag": "database", "freq": 2}, {"tag": "worker", "freq": 1}],
        "based_on_version": 1,
        "reason": None,
    }


@pytest.mark.parametrize("k", [0, MAX_K + 1])
def test_related_rejects_k_outside_supported_range(db_client: TestClient, k: int):
    source_id = upload(db_client, filename="source.txt", content="범위 검증")

    response = db_client.get(
        f"/api/documents/{source_id}/related", params={"k": k}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("limit", [0, 21])
def test_tag_suggestions_reject_limit_outside_supported_range(
    db_client: TestClient, limit: int
):
    source_id = upload(db_client, filename="source.txt", content="범위 검증")

    response = db_client.get(
        f"/api/documents/{source_id}/tag-suggestions", params={"limit": limit}
    )

    assert response.status_code == 422
