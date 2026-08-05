import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.retry import RetryOnOperationalError


def build_app(path: str, method: str, failures: int) -> tuple[TestClient, list[dict]]:
    """지정한 횟수만큼 OperationalError를 낸 뒤 성공하는 앱과, 도착한 본문 기록을 준다."""
    app = FastAPI()
    app.add_middleware(RetryOnOperationalError)
    seen: list[dict] = []

    async def endpoint(body: dict | None = None) -> dict:
        seen.append(body or {})
        if len(seen) <= failures:
            raise psycopg.OperationalError("연결이 끊겼습니다.")
        return {"attempts": len(seen)}

    app.add_api_route(path, endpoint, methods=[method])
    return TestClient(app), seen


def test_read_request_is_retried_once_and_then_succeeds():
    client, seen = build_app("/api/documents", "GET", failures=1)

    response = client.get("/api/documents")

    assert response.status_code == 200
    assert len(seen) == 2


def test_search_is_retried_with_its_request_body_replayed():
    """POST /api/search는 메서드만 POST인 읽기다. 재시도하려면 본문이 다시 읽혀야 한다."""
    client, seen = build_app("/api/search", "POST", failures=1)

    response = client.post("/api/search", json={"query": "정합성"})

    assert response.status_code == 200
    assert seen == [{"query": "정합성"}, {"query": "정합성"}]


def test_write_request_is_not_retried():
    """COMMIT 성공 여부를 알 수 없으므로 쓰기는 재시도하지 않는다 — 문서가 두 번 생길 수 있다."""
    client, seen = build_app("/api/documents", "POST", failures=1)

    with pytest.raises(psycopg.OperationalError):
        client.post("/api/documents", json={"title": "문서"})

    assert len(seen) == 1


def test_a_second_failure_is_not_retried_again():
    client, seen = build_app("/api/documents", "GET", failures=2)

    with pytest.raises(psycopg.OperationalError):
        client.get("/api/documents")

    assert len(seen) == 2


def test_unrelated_errors_are_not_retried():
    app = FastAPI()
    app.add_middleware(RetryOnOperationalError)
    seen: list[int] = []

    @app.get("/api/documents")
    async def endpoint() -> dict:
        seen.append(1)
        raise RuntimeError("버그")

    with pytest.raises(RuntimeError), TestClient(app) as client:
        client.get("/api/documents")

    assert len(seen) == 1
