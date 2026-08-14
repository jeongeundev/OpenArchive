import asyncio
import hashlib
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import get_settings
from app.db import close_pool
from app.embeddings.base import EmbeddingProvider
from app.embeddings.fake import FakeProvider
from app.main import app
from app.migrations import run_migrations
from app.services.auth import hash_password
from app.worker import process_once

# 개발 DB(openarchive)와 분리한다. 테스트는 매번 스키마를 통째로 비우므로
# 같은 DB를 쓰면 개발 데이터가 사라진다.
TEST_DB_NAME = "openarchive_test"


def swap_dbname(dsn: str, dbname: str) -> str:
    """DSN에서 데이터베이스 이름만 바꾼다 — 호스트·포트·자격증명은 그대로 물려받는다.

    로컬 컨테이너 전제다. OpenProxy 경유 DSN에서는 dbname 자리가 데이터베이스가 아니라
    **pool_name**이므로(`.env.example` 참조) 이 치환이 존재하지 않는 풀을 가리키고,
    transaction pooling 위에서 CREATE/DROP DATABASE가 도는지도 확인된 바 없다.
    """
    params = conninfo_to_dict(dsn)
    params["dbname"] = dbname
    return make_conninfo(**params)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings()는 캐시되므로, 환경변수를 바꾸는 테스트끼리 서로 오염될 수 있다.

    매 테스트 전후로 비워 각 테스트가 자기가 설정한 환경만 보게 한다.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    # 컨텍스트 매니저로 쓰지 않으므로 lifespan이 돌지 않는다. 의도된 것이다 —
    # 앱 테스트 전체가 DB에 묶이지 않게 한다. lifespan 자체를 보는 테스트는
    # test_main.py에서 `with TestClient(app)`으로 명시적으로 태운다.
    return TestClient(app)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    """테스트 전용 DB를 세션마다 새로 만들고 그 DSN을 반환한다."""
    admin_dsn = get_settings().database_url
    dsn = swap_dbname(admin_dsn, TEST_DB_NAME)

    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            # 남아 있는 DB를 재사용하지 않고 매번 새로 만든다.
            # clean_db가 public 스키마를 갈아 끼울 때 vector 확장이 함께 드롭되지 않고
            # 사라진 스키마를 가리키는 고아 레코드로 남는 경우가 있다. 그러면
            # CREATE EXTENSION IF NOT EXISTS가 "이미 있다"고 건너뛰어, 이후 모든 DB
            # 테스트가 'type "vector" does not exist'로 죽는다. 그 상태는 DROP EXTENSION으로
            # 복구되지 않는다 — 실제로 시도하면 서버가 죽는다. DB째로 새로 만드는 것이
            # 유일하게 확실한 복구 경로이고, 세션당 한 번이라 비용도 작다.
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    except psycopg.OperationalError as exc:
        # skip하지 않는다. 트리거·SKIP LOCKED·vector 연산자는 실제 DB에서만
        # 검증되므로, 조용한 skip은 "검증했다"는 거짓 신호가 된다.
        pytest.fail(
            f"테스트 DB에 접속할 수 없습니다: {exc}\n"
            f"  DSN(관리 연결): {admin_dsn}\n"
            "  프로젝트 루트에서 `docker compose up -d` 후 `docker compose ps`로 healthy를 확인하세요.",
            pytrace=False,
        )

    return dsn


@pytest.fixture
def clean_db(test_dsn: str) -> str:
    """스키마가 비워진 테스트 DB의 DSN. 매 테스트마다 초기화된다.

    테이블을 하나씩 지우는 대신 스키마를 통째로 갈아 끼운다. 격리 규칙이
    "이 DB에는 직전 테스트의 흔적이 없다" 한 줄로 끝나고, 빈 스키마라 비용도 없다.
    """
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")

    return test_dsn


@pytest.fixture
async def migrated_db(clean_db: str) -> str:
    """실제 backend/migrations/ 를 적용한 테스트 DB의 DSN.

    테스트가 스키마를 자체 SQL로 만들면 검증 대상이 테스트 코드가 되어버린다.
    러너를 그대로 태워, 심사 산출물인 마이그레이션 파일 자체를 검증 대상으로 삼는다.
    """
    await run_migrations(clean_db)
    return clean_db


@pytest.fixture
def db_client(monkeypatch, migrated_db: str) -> TestClient:
    """테스트 DB로 lifespan을 실행하는 API 클라이언트."""
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()

    with TestClient(app) as started:
        yield started

    # app.db의 풀은 모듈 전역이므로 다음 테스트에 DSN이 누수되지 않게 닫는다.
    import asyncio

    asyncio.run(close_pool())


async def insert_test_document(
    conn: psycopg.AsyncConnection,
    *,
    title: str,
    content: str,
    owner_id: str = "alice",
    visibility: str = "public",
    content_type: str = "md",
    tags: list[str] | None = None,
    document_id: UUID | None = None,
) -> UUID:
    """문서를 넣고 ID를 반환한다. 잡과 버전 이력은 실제 트리거가 만든다."""
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    cur = await conn.execute(
        """
        INSERT INTO documents
            (id, title, content_type, content, content_hash, owner_id, visibility, tags)
        VALUES (COALESCE(%s, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (document_id, title, content_type, content, content_hash, owner_id, visibility, tags or []),
    )
    return (await cur.fetchone())[0]


async def process_all_embedding_jobs(
    conn: psycopg.AsyncConnection, provider: EmbeddingProvider
) -> int:
    """대기 중인 임베딩 잡을 실제 워커로 모두 처리하고 처리 건수를 반환한다."""
    processed = 0
    while await process_once(conn, provider):
        processed += 1
    return processed


def run_embedding_worker(dsn: str) -> int:
    """동기 API 테스트에서 워커를 한 번 돌린다.

    TestClient는 동기라 잡이 처리되는 순간을 테스트가 직접 정해야 한다. 워커를
    백그라운드로 띄우면 "아직 pending"과 "이미 ready"를 구분할 수 없어진다.
    """

    async def process() -> int:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            return await process_all_embedding_jobs(conn, FakeProvider())

    return asyncio.run(process())


def upload_document(
    client: TestClient,
    *,
    filename: str = "guide.txt",
    content: bytes = b"OpenSQL guide",
    user_id: str | None = "alice",
    data: dict[str, str | list[str]] | None = None,
):
    """업로드 요청을 보내고 응답을 그대로 돌려준다. 상태 코드 판정은 호출부의 몫이다."""
    if user_id is None:
        client.post("/api/auth/logout")
    else:
        login_as(client, user_id)
    return client.post(
        "/api/documents",
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )


def login_as(client: TestClient, username: str, password: str = "test-password") -> None:
    """실제 로그인 API로 테스트 사용자를 만들고 쿠키 세션을 설정한다."""
    current = client.get("/api/auth/me").json()
    if current.get("authenticated") and current.get("username") == username:
        return
    dsn = get_settings().database_url
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash)
            VALUES (%s, %s)
            ON CONFLICT (username) DO NOTHING
            """,
            (username, hash_password(password)),
        )
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
