import logging

import psycopg
import pytest
from conftest import upload_document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openarchive.api.retry import RetryOnUnavailable

# #110 B에서 장애 구간 응답이 전부 이것이었다 — OpenProxy가 돌려준 SQLSTATE 58000.
ALL_SERVERS_DOWN = psycopg.errors.lookup("58000")(
    "could not get connection from the pool - AllServersDown"
)


def build_app(
    path: str,
    method: str,
    failures: int,
    error: Exception | None = None,
) -> tuple[TestClient, list[dict]]:
    """지정한 횟수만큼 error(기본은 연결 끊김)를 낸 뒤 성공하는 앱과, 도착한 본문 기록을 준다."""
    error = error or psycopg.OperationalError("연결이 끊겼습니다.")
    app = FastAPI()
    app.add_middleware(RetryOnUnavailable)
    seen: list[dict] = []

    async def endpoint(body: dict | None = None) -> dict:
        seen.append(body or {})
        if len(seen) <= failures:
            raise error
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


def test_write_request_is_not_retried_but_answers_503():
    """COMMIT 성공 여부를 알 수 없으므로 쓰기는 재시도하지 않는다 — 문서가 두 번 생길 수 있다.
    그래도 "기다리면 풀린다"는 사실은 알린다 (ADR-048 결정 3)."""
    client, seen = build_app("/api/documents", "POST", failures=1)

    response = client.post("/api/documents", json={"title": "문서"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert len(seen) == 1


@pytest.mark.parametrize("path", ["/api/documents", "/api/documents/text"])
def test_document_creation_with_an_idempotency_key_is_retried_with_its_body_replayed(path):
    """키가 있으면 첫 시도가 커밋됐어도 재시도가 처음 문서를 돌려받는다 (ADR-048 결정 4)."""
    client, seen = build_app(path, "POST", failures=1)

    response = client.post(path, json={"title": "문서"}, headers={"Idempotency-Key": "k-1"})

    assert response.status_code == 200
    assert seen == [{"title": "문서"}, {"title": "문서"}]


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/documents/00000000-0000-0000-0000-000000000000", "PUT"),
        ("/api/documents/00000000-0000-0000-0000-000000000000/reembed", "POST"),
    ],
)
def test_other_writes_are_not_retried_even_with_an_idempotency_key(path, method):
    """키를 지키는 것은 문서 생성뿐이다. 다른 쓰기는 헤더가 붙어 와도 재시도하면 두 번 실행된다."""
    client, seen = build_app(path, method, failures=1)

    response = client.request(
        method, path, json={"title": "문서"}, headers={"Idempotency-Key": "k-1"}
    )

    assert response.status_code == 503
    assert len(seen) == 1


def test_a_second_failure_is_not_retried_again_and_answers_503():
    """즉시 1회로는 10~40초 중단을 덮지 못한다(#110 B-5). 긴 재시도는 클라이언트 몫이다."""
    client, seen = build_app("/api/documents", "GET", failures=2)

    response = client.get("/api/documents")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert isinstance(response.json()["detail"], str)
    assert len(seen) == 2


def test_openproxy_all_servers_down_answers_503_not_500():
    """#110 B-5: 장애 구간 응답이 전부 500이었다."""
    client, seen = build_app("/api/search", "POST", failures=2, error=ALL_SERVERS_DOWN)

    response = client.post("/api/search", json={"query": "정합성"})

    assert response.status_code == 503
    assert len(seen) == 2


def test_write_routed_to_a_replica_is_retried_like_a_lost_connection():
    """승격 직후의 25006(#110 B-4)은 OperationalError가 아니지만 같은 분류다."""
    error = psycopg.errors.lookup("25006")("읽기 전용 트랜잭션에서는 INSERT 명령을 실행할 수 없습니다.")
    client, seen = build_app("/api/documents", "GET", failures=1, error=error)

    response = client.get("/api/documents")

    assert response.status_code == 200
    assert len(seen) == 2


def test_statement_timeout_is_a_500_and_is_not_retried():
    """느린 쿼리는 기다려도 풀리지 않는다 — 503을 주면 클라이언트가 같은 쿼리를 되풀이한다."""
    error = psycopg.errors.lookup("57014")("canceling statement due to statement timeout")
    client, seen = build_app("/api/documents", "GET", failures=1, error=error)

    with pytest.raises(psycopg.errors.QueryCanceled):
        client.get("/api/documents")

    assert len(seen) == 1


def test_a_503_still_leaves_the_error_in_the_log(caplog):
    """응답을 503으로 바꿔도 원인은 운영자가 봐야 한다 — 삼키면 로그에서 장애가 사라진다."""
    client, _ = build_app("/api/documents", "GET", failures=2, error=ALL_SERVERS_DOWN)

    with caplog.at_level(logging.WARNING, logger="openarchive.api.retry"):
        client.get("/api/documents")

    assert "AllServersDown" in caplog.text


def test_upload_on_a_read_only_primary_answers_503(db_client, migrated_db):
    """실제 스택 전체: 풀 연결이 끊긴 뒤 새 연결이 읽기 전용 서버에 붙은 상황(#110 B-4 —
    승격 직후 쓰기가 아직 replica로 간 경우). 서버가 돌려준 25006이 503이 되어야 한다."""
    dbname = psycopg.conninfo.conninfo_to_dict(migrated_db)["dbname"]
    # 로그인도 쓰기(세션 저장)라 읽기 전용으로 바꾸기 전에 끝낸다.
    assert upload_document(db_client, content=b"before").status_code == 201
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(f'ALTER DATABASE "{dbname}" SET default_transaction_read_only = on')
        # 풀이 쥔 연결을 끊어, 다음 요청이 설정이 적용된 새 연결을 받게 한다.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
    try:
        response = db_client.post(
            "/api/documents",
            files={"file": ("b4.txt", b"B-4", "application/octet-stream")},
        )
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 이 연결도 읽기 전용으로 열린다 — 세션에서 먼저 풀어야 되돌릴 수 있다.
            conn.execute("SET default_transaction_read_only = off")
            conn.execute(f'ALTER DATABASE "{dbname}" RESET default_transaction_read_only')

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


def test_unrelated_errors_are_not_retried():
    app = FastAPI()
    app.add_middleware(RetryOnUnavailable)
    seen: list[int] = []

    @app.get("/api/documents")
    async def endpoint() -> dict:
        seen.append(1)
        raise RuntimeError("버그")

    with pytest.raises(RuntimeError), TestClient(app) as client:
        client.get("/api/documents")

    assert len(seen) == 1
