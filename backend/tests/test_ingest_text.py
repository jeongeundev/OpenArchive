"""`examples/ingest_text.py`(외부 공급 클라이언트 예제)의 계약을 고정한다.

예제가 실제 서버에 접속해 완주하는지는 CI가 확인하지 않는다 (ADR-035 트레이드오프 1).
여기서 지키는 것은 예제가 쓰는 경로가 실제로 등록돼 있다는 것과, 폴링이 임베딩 완료를
실제로 기다린다는 것 두 가지다. `sys.path` 조작을 이 파일에 가둬 두는 목적도 겸한다
(`test_seed.py`가 `scripts/`에 대해 쓰는 것과 같은 방식이다).
"""

import sys
from pathlib import Path

import psycopg
from conftest import login_as, run_embedding_worker
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from examples.ingest_text import DOCUMENT_PATH, INGEST_PATH, LOGIN_PATH, is_in_progress

from app.main import app


def test_example_paths_are_registered_routes():
    def collect_paths(routes) -> set[str]:
        paths = {route.path for route in routes if hasattr(route, "path")}
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                paths.update(collect_paths(included.routes))
        return paths

    registered_paths = collect_paths(app.routes)

    assert {LOGIN_PATH, INGEST_PATH, DOCUMENT_PATH} <= registered_paths


def test_polling_waits_through_every_unfinished_status(
    db_client: TestClient, migrated_db: str
):
    """폴링이 워커가 거치는 중간 상태를 모두 기다린다.

    `pending`만 조건에 두면 워커가 잡을 집는 순간 `processing`이 되어 루프를 빠져나가고,
    예제가 임베딩이 끝나지 않은 문서를 최종 결과로 출력한다.
    """
    login_as(db_client, "alice")
    document = db_client.post(
        INGEST_PATH, json={"title": "Polling", "content": "polling target"}
    ).json()

    assert document["embedding_status"] == "pending"
    assert is_in_progress(document)

    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE documents SET embedding_status = 'processing' WHERE id = %s",
            (document["id"],),
        )
    detail_path = DOCUMENT_PATH.format(document_id=document["id"])
    assert is_in_progress(db_client.get(detail_path).json())

    run_embedding_worker(migrated_db)
    finished = db_client.get(detail_path).json()
    assert finished["embedding_status"] == "ready"
    assert not is_in_progress(finished)
