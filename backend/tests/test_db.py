"""`openarchive.db`의 import 부작용 부재를 검증한다 (ADR-012).

MCP 서버는 `openarchive.services`를 직접 import하고(ADR-008) 워커도 같은 패키지를 쓴다.
import가 곧 접속이면 세 프로세스가 의도치 않게 각자 풀을 연다.
그래서 여기서는 "접속이 되는가"가 아니라 **"import만으로는 아무 일도 일어나지 않는가"**를 본다.
DB 컨테이너 없이 통과해야 한다.
"""

import importlib

import psycopg
import psycopg_pool
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool

import openarchive.db
import openarchive.services.documents
from openarchive.config import get_settings


@pytest.fixture(autouse=True)
def _restore_db_module():
    """모듈 전역 상태(_pool)와 패치된 클래스가 테스트 간에 새지 않게 되돌린다."""
    yield
    importlib.reload(openarchive.db)


@pytest.fixture
def pool_spy(monkeypatch):
    """AsyncConnectionPool의 인스턴스화를 관찰한다.

    "풀이 언제 만들어지는가"가 이 모듈의 검증 대상이므로, 생성 자체를 세는 것이
    가장 직접적이다. 패치 후 reload해야 모듈이 스파이 클래스를 집어간다.
    """
    created = []

    class SpyPool:
        @staticmethod
        async def check_connection(_connection):
            pass

        def __init__(self, conninfo="", **kwargs):
            self.conninfo = conninfo
            self.kwargs = kwargs
            created.append(self)

        async def close(self):
            pass

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", SpyPool)
    importlib.reload(openarchive.db)
    return created


def test_import_alone_creates_no_pool(monkeypatch, pool_spy):
    """DATABASE_URL이 없어도 import가 성공하고, 그것만으로 풀이 만들어지지 않는다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    importlib.reload(openarchive.db)

    assert pool_spy == []
    assert openarchive.db._pool is None


def test_get_pool_creates_lazily_and_reuses(pool_spy):
    assert pool_spy == []

    pool = openarchive.db.get_pool()

    assert len(pool_spy) == 1
    assert openarchive.db.get_pool() is pool
    assert len(pool_spy) == 1


def test_pool_is_created_with_auto_open_disabled(pool_spy):
    """psycopg_pool은 기본적으로 생성 시점에 열린다. 껐는지 확인한다.

    켜져 있으면 get_pool()을 부르는 순간 커넥션이 생겨, 여는 시점을 호출부가
    통제할 수 없게 된다.
    """
    openarchive.db.get_pool()

    assert pool_spy[0].kwargs["open"] is False


def test_pool_checks_connections_when_borrowed(pool_spy):
    openarchive.db.get_pool()

    assert pool_spy[0].kwargs["check"] is openarchive.db.AsyncConnectionPool.check_connection


def test_pool_uses_dsn_from_settings(monkeypatch, pool_spy):
    """DSN은 환경변수로만 주입된다 — 코드에 박지 않는다 (ADR-006)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@openproxy.example:6432/pool_a")
    get_settings.cache_clear()

    openarchive.db.get_pool()

    assert pool_spy[0].conninfo == "postgresql://app@openproxy.example:6432/pool_a"


def test_real_pool_is_not_open_on_creation():
    """스파이가 아닌 실제 AsyncConnectionPool로도 확인한다."""
    pool = openarchive.db.get_pool()

    assert isinstance(pool, AsyncConnectionPool)
    assert pool.closed


async def test_close_pool_resets_state(pool_spy):
    pool = openarchive.db.get_pool()

    await openarchive.db.close_pool()

    assert openarchive.db._pool is None
    assert openarchive.db.get_pool() is not pool
    assert len(pool_spy) == 2


async def test_close_pool_without_a_pool_is_noop():
    """풀을 만든 적 없는 프로세스가 종료돼도 예외가 나지 않아야 한다."""
    await openarchive.db.close_pool()

    assert openarchive.db._pool is None


def _effective_params(pool) -> dict:
    """풀이 새 연결을 열 때 libpq에 넘길 최종 설정 — DSN 위에 풀의 kwargs가 얹힌다."""
    return conninfo_to_dict(make_conninfo(pool.conninfo, **pool.kwargs.get("kwargs", {})))


def test_pool_connections_detect_a_dead_peer_within_a_minute(monkeypatch, pool_spy):
    """keepalive를 코드 기본값으로 건다 — DSN에 붙이는 것을 잊어도 B-1이 재발하지 않게 (ADR-048).

    VIP가 원래 노드로 돌아가는 순간 응답을 기다리던 연결은 FIN도 RST도 받지 못한다. OS 기본
    keepalive로는 약 2시간 뒤에야 끊김을 알아, HA 실측에서 워커가 영구 정지했다(#110 B-1).
    DSN 문자열은 건드리지 않는다 — 여전히 환경변수 하나, 호스트 하나다(ADR-006).
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://app@openproxy.example:6432/pool_a")
    get_settings.cache_clear()

    openarchive.db.get_pool()

    assert pool_spy[0].conninfo == "postgresql://app@openproxy.example:6432/pool_a"
    params = _effective_params(pool_spy[0])
    assert params["keepalives_idle"] == "30"
    assert params["keepalives_interval"] == "10"
    assert params["keepalives_count"] == "3"
    assert params["tcp_user_timeout"] == "60000"


def test_keepalive_written_in_the_dsn_wins_over_the_defaults(monkeypatch, pool_spy):
    """기본값은 비어 있는 자리만 채운다 — 운영자가 DSN에 적은 값을 덮어쓰면 기본값이 아니다."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://app@openproxy.example:6432/pool_a?keepalives_idle=5&tcp_user_timeout=0",
    )
    get_settings.cache_clear()

    openarchive.db.get_pool()

    params = _effective_params(pool_spy[0])
    assert params["keepalives_idle"] == "5"
    assert params["tcp_user_timeout"] == "0"
    assert params["keepalives_count"] == "3"


# ── 일시 불가용 분류 (ADR-048 결정 3) ──────────────────────────────────────────
# 오류는 실제 서버가 돌려준 것으로 만든다. OpenProxy 오류는 #110 B 로그
# (`notes/ha110/b/out/*.log`)에 찍힌 클래스(psycopg `SystemError` — SQLSTATE 58000에만
# 대응한다)와 문구를 그대로 서버에서 RAISE해 같은 경로로 올라오게 한다.


async def _server_error(dsn: str, sql: str) -> psycopg.Error:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        try:
            await conn.execute(sql)
        except psycopg.Error as error:
            return error
    raise AssertionError(f"오류가 나지 않았다: {sql}")


def _raise_sql(sqlstate: str, message: str) -> str:
    return f"DO $$ BEGIN RAISE EXCEPTION '{message}' USING ERRCODE = '{sqlstate}'; END $$"


@pytest.mark.parametrize(
    "message",
    [
        "could not get connection from the pool - AllServersDown",
        (
            'error receiving data from server: SocketError("Error reading message code from socket'
            ' - Error Os { code: 110, kind: TimedOut, message: \\"Connection timed out\\" }")'
        ),
        (
            'error receiving data from server: SocketError("Error reading message code from socket'
            ' - Error Os { code: 104, kind: ConnectionReset, message: \\"Connection reset by peer\\" }")'
        ),
        (
            'error receiving data from server: SocketError("Error reading message code from socket'
            ' - Error Kind(UnexpectedEof)")'
        ),
    ],
)
async def test_openproxy_backend_errors_are_unavailable(test_dsn, message):
    error = await _server_error(test_dsn, _raise_sql("58000", message.replace("'", "''")))

    assert isinstance(error, psycopg.errors.SystemError)
    assert openarchive.db.is_unavailable(error)


async def test_write_routed_to_a_replica_during_promotion_is_unavailable(test_dsn):
    """승격 직후 쓰기가 아직 replica로 간 경우(#110 B-4). 우리 설계에서 쓰기가 replica로
    가는 것은 라우팅 전환 중뿐이다."""
    error = await _server_error(
        test_dsn, "BEGIN READ ONLY; CREATE TABLE ro_probe (x int); COMMIT"
    )

    assert isinstance(error, psycopg.errors.ReadOnlySqlTransaction)
    assert openarchive.db.is_unavailable(error)


async def test_terminated_backend_is_unavailable(test_dsn):
    """Primary가 내려가며 세션을 끊는 경우(57P01)."""
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as victim:
        async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as killer:
            await killer.execute("SELECT pg_terminate_backend(%s)", [victim.info.backend_pid])
        with pytest.raises(psycopg.OperationalError) as caught:
            await victim.execute("SELECT 1")

    assert openarchive.db.is_unavailable(caught.value)


async def test_lost_connection_is_unavailable(test_dsn):
    """끊긴 연결을 다시 쓰면 서버 응답 없이 클라이언트가 올리는 오류(SQLSTATE 없음) —
    #110 B 로그의 `the connection is lost`."""
    async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as victim:
        async with await psycopg.AsyncConnection.connect(test_dsn, autocommit=True) as killer:
            await killer.execute("SELECT pg_terminate_backend(%s)", [victim.info.backend_pid])
        with pytest.raises(psycopg.OperationalError):
            await victim.execute("SELECT 1")
        with pytest.raises(psycopg.OperationalError) as caught:
            await victim.execute("SELECT 1")

    assert caught.value.sqlstate is None
    assert openarchive.db.is_unavailable(caught.value)


def test_pool_timeout_is_unavailable():
    """장애 중 풀이 연결을 내주지 못하는 경우. 서버 응답이 없어 SQLSTATE도 없다."""
    assert openarchive.db.is_unavailable(psycopg_pool.PoolTimeout("couldn't get a connection"))


@pytest.mark.parametrize(
    "sqlstate",
    [
        "53100",  # disk_full — 공간을 비워야 풀린다
        "54001",  # statement_too_complex — 쿼리 결함
        "57P04",  # database_dropped
        "58P01",  # undefined_file
        "28P01",  # invalid_password — 설정 결함
    ],
)
async def test_permanent_operational_errors_are_not_unavailable(test_dsn, sqlstate):
    """psycopg는 이들도 `OperationalError`로 올린다. 기다려도 풀리지 않으므로 503을 주면
    클라이언트가 60초 백오프 끝에 "일시적"이라는 안내만 보이고 결함이 가려진다."""
    error = await _server_error(test_dsn, _raise_sql(sqlstate, "permanent"))

    assert isinstance(error, psycopg.OperationalError)
    assert not openarchive.db.is_unavailable(error)


def test_cannot_connect_now_is_unavailable():
    """기동·복구 중인 서버가 접속을 거절하는 경우(57P03). 실 서버로 만들 수 없어
    psycopg가 SQLSTATE로 고르는 클래스를 그대로 쓴다."""
    assert openarchive.db.is_unavailable(psycopg.errors.lookup("57P03")())


async def test_statement_timeout_is_not_unavailable(test_dsn):
    """느린 쿼리는 기다려도 풀리지 않는다. 503을 주면 클라이언트가 같은 쿼리를 되풀이한다."""
    error = await _server_error(test_dsn, "SET statement_timeout = 10; SELECT pg_sleep(1)")

    assert isinstance(error, psycopg.errors.QueryCanceled)
    assert not openarchive.db.is_unavailable(error)


async def test_code_defects_are_not_unavailable(test_dsn):
    error = await _server_error(test_dsn, "SELECT * FROM no_such_table")

    assert not openarchive.db.is_unavailable(error)
    assert not openarchive.db.is_unavailable(RuntimeError("버그"))


async def test_pool_query_survives_server_prepared_statement_reset(monkeypatch, migrated_db):
    """OpenProxy에서 서버 명령문 캐시가 사라져도 정상 요청이 26000으로 실패하지 않는다."""
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    pool = openarchive.db.get_pool()
    await pool.open()
    try:
        async with pool.connection() as conn:
            for _ in range(7):
                async with conn.transaction():
                    row = await (await conn.execute("SELECT %s::int + 1", (41,))).fetchone()
                    assert row[0] == 42
            async with conn.transaction():
                await conn.execute("DEALLOCATE ALL", prepare=False)
                row = await (await conn.execute("SELECT %s::int + 1", (41,))).fetchone()
                assert row[0] == 42
    finally:
        await openarchive.db.close_pool()


async def _open_pool(monkeypatch, dsn: str):
    monkeypatch.setenv("DATABASE_URL", dsn)
    get_settings.cache_clear()
    pool = openarchive.db.get_pool()
    await pool.open()
    return pool


async def test_pool_binds_parameters_on_the_client(monkeypatch, migrated_db):
    """풀 연결의 파라미터는 서버가 아니라 클라이언트에서 문장에 들어간다 (#210).

    OpenProxy는 파라미터가 있는 이름 없는 문장을 실행마다 서버 prepared statement로 새로
    만들고 연결이 끝날 때까지 쌓는다. OpenSQL 배포판은 assert 빌드라 문장마다 백엔드 메모리
    전체를 검사하므로, 쌓인 계획만큼 모든 문장이 느려진다. 서버가 받은 문장에 `$1`이 없고
    값이 들어 있어야 프록시가 만들 서버 명령문이 생기지 않는다.
    """
    await _open_pool(monkeypatch, migrated_db)
    try:
        async with openarchive.db.connection() as conn, conn.transaction():
            received = (
                await (
                    await conn.execute("SELECT current_query(), %s::text", ("표식-210",))
                ).fetchone()
            )[0]
    finally:
        await openarchive.db.close_pool()

    assert "$1" not in received
    assert "표식-210" in received


@pytest.mark.parametrize(
    "value",
    [
        "it's",
        "\\x00 \\' \\\\",
        "'); DROP TABLE documents; --",
        "$$; SELECT 1; $$",
        "E'\\n' 한글 🙂",
        None,
    ],
)
async def test_pool_round_trips_hostile_values_unchanged(monkeypatch, migrated_db, value):
    """클라이언트 측 바인딩에서도 값은 문장이 아니라 값으로만 전달된다 (#210)."""
    await _open_pool(monkeypatch, migrated_db)
    try:
        async with openarchive.db.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    "SELECT %s::text, to_regclass('documents') IS NOT NULL", (value,)
                )
            ).fetchone()
    finally:
        await openarchive.db.close_pool()

    assert row == (value, True)


async def test_pool_round_trips_binary_original_files(monkeypatch, migrated_db):
    """원본 파일 바이트(`%b`)는 클라이언트 측 바인딩 연결에서도 그대로 저장·조회된다 (#210)."""
    data = bytes(range(256)) * 64
    await _open_pool(monkeypatch, migrated_db)
    try:
        async with openarchive.db.connection() as conn, conn.transaction():
            doc_id = (
                await (
                    await conn.execute(
                        "INSERT INTO documents (title, content_type, content, content_hash, owner_id)"
                        " VALUES ('t', 'txt', 'c', md5('c'), 'u210') RETURNING id"
                    )
                ).fetchone()
            )[0]
            await openarchive.services.documents._insert_original_file(
                conn,
                document_id=doc_id,
                file_version=1,
                filename="a.bin",
                data=data,
                text_version=1,
                uploaded_by="u210",
            )
            stored = (
                await (
                    await conn.execute(
                        "SELECT data FROM document_files WHERE document_id = %s", (doc_id,)
                    )
                ).fetchone()
            )[0]
    finally:
        await openarchive.db.close_pool()

    assert bytes(stored) == data
