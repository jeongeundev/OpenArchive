import pytest
from conftest import login_as
from test_search_api import seed_documents

from openarchive.answers import AnswerUnavailable, FakeAnswerProvider
from openarchive.db import get_pool
from openarchive.main import app
from openarchive.services.search import MAX_K

QUERY = {"query": "OpenSQL 정합성", "k": 5}


@pytest.fixture
def evidence(db_client, migrated_db):
    login_as(db_client, "alice")
    ids = seed_documents(migrated_db, [
        {"title": "정합성 규정", "content": "OpenSQL 정합성 트리거 운영 규정", "tags": ["규정"]},
        {"title": "휴가 안내", "content": "연차 휴가 신청 승인 안내"},
    ])
    return ids


def search_items(client):
    response = client.post("/api/search", json=QUERY)
    assert response.status_code == 200
    return response.json()["items"]


def test_ask_returns_answer_sources_and_search_items(db_client, evidence, monkeypatch):
    monkeypatch.setattr(app.state, "answer_provider", FakeAnswerProvider())
    expected = search_items(db_client)
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert "[1]" in body["answer"]
    assert body["detail"] is None
    assert body["items"] == expected
    assert body["sources"][0] == {
        "label": 1, "document_id": evidence[0], "title": "정합성 규정",
        "chunk_index": 0, "based_on_version": 1, "current_version": 1,
        "revised": False, "content": "OpenSQL 정합성 트리거 운영 규정", "cited": True,
    }


def test_ask_disabled_keeps_search_items(db_client, evidence):
    assert app.state.answer_provider is None
    expected = search_items(db_client)
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disabled"
    assert body["answer"] is None
    assert body["items"] == expected


def test_ask_failed_keeps_search_items(db_client, evidence, monkeypatch):
    class Unavailable:
        name = "unavailable"

        def generate(self, system, prompt):
            raise AnswerUnavailable("모델 연결 실패")

    monkeypatch.setattr(app.state, "answer_provider", Unavailable())
    expected = search_items(db_client)
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["answer"] is None
    assert body["detail"] == "답변 생성에 실패했습니다."  # 원인 문구는 서버 로그에만 남긴다
    assert body["items"] == expected


def test_ask_without_evidence_does_not_generate(db_client, evidence, monkeypatch):
    class MustNotGenerate:
        name = "unused"

        def generate(self, system, prompt):
            pytest.fail("빈 근거로 모델을 호출했습니다")

    monkeypatch.setattr(app.state, "answer_provider", MustNotGenerate())
    response = db_client.post("/api/ask", json={**QUERY, "tags": ["없는 태그"]})
    assert response.status_code == 200
    assert response.json()["status"] == "no_evidence"
    assert response.json()["sources"] == []
    assert response.json()["items"] == []


def test_ask_rejects_blank_query(db_client, evidence):
    assert db_client.post("/api/ask", json={"query": "  "}).status_code == 400


def test_ask_requires_login(db_client):
    assert db_client.post("/api/ask", json=QUERY).status_code == 401


@pytest.mark.parametrize("k", [0, MAX_K + 1])
def test_ask_rejects_invalid_k(db_client, evidence, k):
    assert db_client.post("/api/ask", json={**QUERY, "k": k}).status_code == 422


def test_generation_releases_all_pool_connections(db_client, evidence, monkeypatch):
    class Recording(FakeAnswerProvider):
        def __init__(self):
            self.borrowed = []

        def generate(self, system, prompt):
            stats = get_pool().get_stats()
            self.borrowed.append(stats["pool_size"] - stats["pool_available"])
            return super().generate(system, prompt)

    provider = Recording()
    # 같은 관측기가 대여 중인 연결을 실제로 구별하는 기준선.
    async def baseline():
        async with get_pool().connection():
            provider.generate("", "[1] 기준선")
    db_client.portal.call(baseline)
    assert provider.borrowed == [1]
    monkeypatch.setattr(app.state, "answer_provider", provider)
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert provider.borrowed == [1, 0]


def test_ask_accepts_a_read_token(db_client, evidence, monkeypatch):
    response = db_client.post("/api/auth/tokens", json={"name": "ask", "scope": "read"})
    assert response.status_code == 201
    token = response.json()["token"]
    db_client.post("/api/auth/logout")
    monkeypatch.setattr(app.state, "answer_provider", FakeAnswerProvider())
    response = db_client.post("/api/ask", json=QUERY, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["sources"][0]["document_id"] == evidence[0]


def test_authentication_and_evidence_use_one_pool_checkout(db_client, evidence):
    before = get_pool().get_stats()["requests_num"]
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    assert response.json()["items"]
    assert get_pool().get_stats()["requests_num"] - before == 1


def test_ask_applies_structured_filters(db_client, evidence):
    response = db_client.post(
        "/api/ask", json={**QUERY, "tags": ["규정"], "content_type": "md"}
    )
    assert response.status_code == 200
    assert [item["document_id"] for item in response.json()["items"]] == [evidence[0]]


def test_database_retry_generates_only_once(db_client, evidence, monkeypatch):
    import psycopg

    from openarchive.api import ask

    gather = ask.gather_evidence
    attempts = []
    generations = []

    async def interrupted(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise psycopg.OperationalError("연결 끊김")
        return await gather(*args, **kwargs)

    class Recording(FakeAnswerProvider):
        def generate(self, system, prompt):
            generations.append(prompt)
            return super().generate(system, prompt)

    monkeypatch.setattr(ask, "gather_evidence", interrupted)
    monkeypatch.setattr(app.state, "answer_provider", Recording())
    response = db_client.post("/api/ask", json=QUERY)
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert len(attempts) == 2
    assert len(generations) == 1


def test_ask_applies_folder_filter_including_descendants(db_client, migrated_db, monkeypatch):
    import psycopg

    login_as(db_client, "alice")
    folders = []
    for name, parent in [("A", None), ("하위", 0), ("B", None)]:
        response = db_client.post("/api/folders", json={
            "name": name, "parent_id": folders[parent] if parent is not None else None,
        })
        assert response.status_code == 201
        folders.append(response.json()["id"])
    ids = seed_documents(migrated_db, [
        {"title": name, "content": f"OpenSQL 정합성 {name} 근거"}
        for name in ["A", "하위", "B"]
    ])
    with psycopg.connect(migrated_db) as conn:
        # 관계 확장과 분리해 폴더의 직접 검색 범위를 검증한다.
        conn.execute("DELETE FROM document_edges")
        for document_id, folder_id in zip(ids, folders, strict=True):
            conn.execute("UPDATE documents SET folder_id=%s WHERE id=%s", (folder_id, document_id))
    monkeypatch.setattr(app.state, "answer_provider", FakeAnswerProvider())
    for filters, expected in [({}, set(ids)), ({"folder_id": folders[0]}, set(ids[:2]))]:
        response = db_client.post("/api/ask", json={**QUERY, **filters})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert {item["document_id"] for item in body["items"] if item["via"] is None} == expected
        assert {source["document_id"] for source in body["sources"]} == expected

    response = db_client.put(
        f"/api/folders/{folders[2]}/access", json={"visibility": "private"}
    )
    assert response.status_code == 200
    login_as(db_client, "bob")
    response = db_client.post("/api/ask", json={**QUERY, "folder_id": folders[2]})
    assert response.status_code == 200
    assert response.json()["status"] == "no_evidence"
    assert response.json()["items"] == []
    assert response.json()["sources"] == []
