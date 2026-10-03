"""`openarchive init` — 설치 CLI (ADR-039).

실제 pgvector 컨테이너에 붙는다. 이 CLI가 지켜야 하는 것 — 확장 가용성 판정,
기존 스키마와의 충돌 감지, 마이그레이션 적용의 멱등성 — 은 전부 DB가 결정하므로
Mock으로는 확인할 수 없다 (CLAUDE.md 개발 프로세스).
"""

import asyncio
import hashlib
import time
from pathlib import Path

import psycopg
import pytest
from conftest import background_worker
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_triggers import insert_document, mark_document_ready, unit_vector

from openarchive.cli import OWNED_TABLES, main, probe_capabilities
from openarchive.migrations import migration_files, run_migrations
from openarchive.services.auth import hash_password, verify_password
from openarchive.services.documents import create_document


def table_names(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def applied_migrations(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename"
        ).fetchall()
    return [row[0] for row in rows]


@pytest.fixture(autouse=True)
def _no_terminal(monkeypatch):
    """비밀번호 프롬프트는 기본적으로 입력이 닫힌 것으로 둔다.

    getpass는 stdin이 아니라 /dev/tty를 먼저 연다 — 막아 두지 않으면 터미널에서 돌린
    테스트가 비밀번호를 기다리며 멈추고, 개발자 셸의 ADMIN_PASSWORD가 결과를 바꾼다.
    """
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("openarchive.cli.getpass.getpass", _no_stdin)


def accounts(dsn: str) -> list[tuple[str, bool]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT username, is_admin FROM users ORDER BY username").fetchall()


def test_owned_tables_match_the_migration_files():
    """보호 판정의 기준이 되는 목록이므로 마이그레이션과 어긋나면 안 된다.

    새 테이블을 추가하고 이 목록이 따라오지 않으면, 그 이름을 쓰는 남의 테이블을
    감지하지 못한 채 마이그레이션이 ALTER로 손대게 된다.
    """
    assert OWNED_TABLES == {
        "api_tokens",
        "document_chunks",
        "document_edges",
        "document_files",
        "document_grants",
        "document_links",
        "document_versions",
        "documents",
        "embedding_jobs",
        "group_members",
        "groups",
        "idempotency_keys",
        "sessions",
        "shares",
        "users",
    }


def test_init_applies_every_migration_to_a_clean_database(clean_db: str, tmp_path):
    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert applied_migrations(clean_db) == [path.name for path in migration_files()]
    assert "documents" in table_names(clean_db)


def test_init_is_idempotent_on_an_already_prepared_database(migrated_db: str, tmp_path):
    """두 번째 실행은 적용할 것이 없다고 보고하고 성공해야 한다."""
    before = applied_migrations(migrated_db)

    exit_code = main(["init", "--dsn", migrated_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert applied_migrations(migrated_db) == before


def test_init_preserves_existing_documents(migrated_db: str, tmp_path):
    """이미 쓰고 있는 설치에 다시 돌려도 데이터가 사라지지 않는다."""
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO documents "
            "(title, content_type, content, content_hash, owner_id) "
            "VALUES ('보존 확인', 'md', '내용', 'hash-preserve', 'alice')"
        )
        conn.commit()

    exit_code = main(["init", "--dsn", migrated_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    with psycopg.connect(migrated_db) as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM documents WHERE content_hash = 'hash-preserve'"
        ).fetchone()
    assert count == 1


def test_init_refuses_a_database_that_already_has_a_conflicting_table(
    clean_db: str, capsys, tmp_path
):
    """OpenArchive와 무관한 DB에 스키마를 얹지 않는다.

    마이그레이션 009는 `ALTER TABLE documents`를 실행한다. 남의 `documents`를 그대로
    두고 적용하면 그 테이블이 손상된다.
    """
    with psycopg.connect(clean_db) as conn:
        conn.execute("CREATE TABLE documents (id int PRIMARY KEY, note text)")
        conn.execute("INSERT INTO documents (id, note) VALUES (1, '남의 데이터')")
        conn.commit()

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert "documents" in capsys.readouterr().out
    with psycopg.connect(clean_db) as conn:
        (note,) = conn.execute("SELECT note FROM documents WHERE id = 1").fetchone()
        assert note == "남의 데이터"
        assert conn.execute(
            "SELECT to_regclass('public.schema_migrations')"
        ).fetchone() == (None,)


def test_init_reports_a_connection_failure_without_traceback(capsys, tmp_path):
    exit_code = main(
        [
            "init",
            "--dsn",
            "postgresql://nobody:nobody@127.0.0.1:59999/nowhere",
            "--yes",
            "--env-file",
            str(tmp_path / ".env"),
        ]
    )

    assert exit_code == 1
    assert "연결" in capsys.readouterr().out


def test_init_writes_the_dsn_to_the_env_file(clean_db: str, tmp_path):
    env_file = tmp_path / ".env"

    main(["init", "--dsn", clean_db, "--yes", "--env-file", str(env_file)])

    assert f"DATABASE_URL={clean_db}" in env_file.read_text(encoding="utf-8")


def test_init_replaces_only_the_dsn_line_in_an_existing_env_file(clean_db: str, tmp_path):
    """다른 설정을 지우지 않는다 — .env는 사용자가 손으로 관리하는 파일이다."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_PROVIDER=local\nDATABASE_URL=postgresql://old@localhost:5433/old\n"
        "SESSION_LIFETIME_HOURS=48\n",
        encoding="utf-8",
    )

    main(["init", "--dsn", clean_db, "--yes", "--env-file", str(env_file)])

    written = env_file.read_text(encoding="utf-8")
    assert f"DATABASE_URL={clean_db}" in written
    assert "postgresql://old@localhost:5433/old" not in written
    assert "EMBEDDING_PROVIDER=local" in written
    assert "SESSION_LIFETIME_HOURS=48" in written


@pytest.mark.parametrize("extension", ["vector", "pg_trgm"])
def test_capability_probe_finds_the_required_extensions(clean_db: str, extension: str):
    with psycopg.connect(clean_db) as conn:
        capabilities = probe_capabilities(conn)

    assert capabilities.extensions[extension] is True
    assert capabilities.server_version_num >= 130000
    assert capabilities.can_create is True


def test_capability_probe_reads_schema_level_create_privilege(clean_db: str):
    """CREATE TABLE 가능 여부는 DB가 아니라 스키마 권한이 정한다.

    has_database_privilege(..., 'CREATE')는 **DB에 스키마를 만들 권한**이라, public에만
    CREATE를 받은 롤에서 false가 된다. 그 롤은 마이그레이션을 정상 적용할 수 있으므로
    그 함수로 판정하면 멀쩡한 DB를 거부한다.
    """
    params = conninfo_to_dict(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("DROP ROLE IF EXISTS cli_probe_role")
        conn.execute("CREATE ROLE cli_probe_role LOGIN PASSWORD 'probe'")
        conn.execute(f'GRANT CONNECT ON DATABASE "{params["dbname"]}" TO cli_probe_role')
        # clean_db가 public을 새로 만들어 PUBLIC 롤의 기본 USAGE가 없다. 실제 DB에는
        # 있으므로, 판정 대상(CREATE 권한)만 남기려면 여기서 되돌려 놓아야 한다.
        conn.execute("GRANT USAGE ON SCHEMA public TO cli_probe_role")
        conn.execute("GRANT CREATE ON SCHEMA public TO cli_probe_role")
    try:
        limited = make_conninfo(**{**params, "user": "cli_probe_role", "password": "probe"})
        with psycopg.connect(limited) as conn:
            capabilities = probe_capabilities(conn)
            # 판정이 맞다면 이 롤은 실제로 테이블을 만들 수 있어야 한다.
            conn.execute("CREATE TABLE cli_probe_table (id int)")
            conn.rollback()
        assert capabilities.can_create is True
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute(f'REVOKE CONNECT ON DATABASE "{params["dbname"]}" FROM cli_probe_role')
            conn.execute("REVOKE ALL ON SCHEMA public FROM cli_probe_role")
            conn.execute("DROP ROLE IF EXISTS cli_probe_role")


def test_init_proceeds_when_a_dba_already_installed_pg_trgm(clean_db: str, tmp_path):
    """확장은 스키마가 아니라 DB 전체에 하나다 — 조직 DB에는 `pg_trgm`이 이미 있는 일이 흔하다.

    005가 `IF NOT EXISTS` 없이 만들던 동안 init은 이 DB를 "DROP EXTENSION 하거나 새 DB를
    쓰라"며 거부했다. `--schema`가 겨냥하는 조직 DB에서 `--schema`가 막히는 셈이었다 (#95-c).
    """
    with psycopg.connect(clean_db) as conn:
        conn.execute("CREATE EXTENSION pg_trgm")
        conn.commit()

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert applied_migrations(clean_db) == [path.name for path in migration_files()]


@pytest.fixture
def non_superuser_dsn(clean_db: str):
    """DBA가 내준 앱 전용 롤로 붙는 DSN — OpenSQL 기사용자의 실제 도입 형태다.

    설치기가 만든 postgres 슈퍼유저로만 검증하면 이 경로가 통째로 가려진다. 롤에는
    테이블 생성(스키마 CREATE)과 trusted 확장 생성(DB CREATE)까지 주고 슈퍼유저 권한만
    주지 않는다 — 실측상 이 롤은 pg_trgm은 만들 수 있고 vector는 만들 수 없다.
    """
    params = conninfo_to_dict(clean_db)
    dbname = params["dbname"]
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("DROP ROLE IF EXISTS cli_app_role")
        conn.execute("CREATE ROLE cli_app_role LOGIN PASSWORD 'app-role'")
        conn.execute(f'GRANT CONNECT, CREATE ON DATABASE "{dbname}" TO cli_app_role')
        # clean_db가 public을 새로 만들어 PUBLIC 롤의 기본 USAGE가 없다. 실제 DB에는
        # 있으므로 판정 대상(확장 생성 권한)만 남기려면 여기서 되돌려 놓아야 한다.
        conn.execute("GRANT USAGE, CREATE ON SCHEMA public TO cli_app_role")
    try:
        yield make_conninfo(**{**params, "user": "cli_app_role", "password": "app-role"})
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute("DROP OWNED BY cli_app_role CASCADE")
            conn.execute("DROP ROLE IF EXISTS cli_app_role")


def test_capability_probe_sees_that_an_untrusted_extension_needs_a_superuser(
    non_superuser_dsn: str,
):
    """`vector`는 trusted=false라 슈퍼유저만 만들 수 있다 (로컬 컨테이너 실측).

    설치 **가능** 여부(pg_available_extensions)만 보면 이 롤도 통과한다. 그 판정으로는
    001의 CREATE EXTENSION vector가 적용 단계에 가서야 InsufficientPrivilege로 죽는다.
    """
    with psycopg.connect(non_superuser_dsn) as conn:
        capabilities = probe_capabilities(conn)

    assert capabilities.extensions["vector"] is True
    assert "vector" not in capabilities.installed_extensions
    assert "vector" not in capabilities.creatable_extensions
    # trusted 확장은 DB CREATE 권한만으로 만들 수 있다. 함께 거부하면 과잉 거부다.
    assert "pg_trgm" in capabilities.creatable_extensions


def test_init_stops_before_applying_when_the_role_cannot_create_an_extension(
    non_superuser_dsn: str, clean_db: str, capsys, tmp_path
):
    """"확인이 적용보다 먼저"(ADR-039)가 비슈퍼유저 경로에서 깨져 있었다.

    capability 점검이 통과시킨 뒤 001에서 traceback이 나고 schema_migrations만 남았다.
    """
    exit_code = main(
        ["init", "--dsn", non_superuser_dsn, "--yes", "--env-file", str(tmp_path / ".env")]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "vector" in output
    assert "Traceback" not in output
    # 아무것도 적용하지 않았어야 한다 — 이력 테이블 하나도 남기지 않는다.
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)


def test_init_proceeds_when_a_dba_preinstalled_the_untrusted_extension(
    non_superuser_dsn: str, clean_db: str, tmp_path
):
    """DBA가 vector만 미리 깔아주는 것이 기사용자의 정상 경로다.

    권한이 없다고 일괄 거부하면 이 경로까지 막힌다 — 이미 설치된 확장은 001이
    IF NOT EXISTS로 넘어가므로 만들 권한이 필요 없다.
    """
    with psycopg.connect(clean_db) as conn:  # 슈퍼유저(DBA) 자격으로 미리 설치한다
        conn.execute("CREATE EXTENSION vector")
        conn.commit()

    exit_code = main(
        ["init", "--dsn", non_superuser_dsn, "--yes", "--env-file", str(tmp_path / ".env")]
    )

    assert exit_code == 0
    assert applied_migrations(non_superuser_dsn) == [path.name for path in migration_files()]


def test_a_non_superuser_can_apply_every_migration_as_an_upgrade(
    non_superuser_dsn: str, clean_db: str, tmp_path
):
    """마이그레이션 하나하나를 **새 세션에서 처음 적용되는 파일**로 적용해도 통과한다.

    새 설치는 한 세션에서 001부터 적용하므로 앞 파일이 pgvector 라이브러리를 이미 로드한다.
    업그레이드(`pip install -U` 뒤 `init`)는 새 파일만 적용하는데, 라이브러리가 아직 로드되지
    않은 세션에서 함수 정의의 `SET hnsw.ef_search`는 정의되지 않은 자리표시자라 비슈퍼유저에게
    거부된다 — 실 OpenSQL VM에서 023 → 024 업그레이드가 InsufficientPrivilege로 멈췄다.
    로컬 컨테이너의 기본 롤은 슈퍼유저라 이 경로가 가려져 있었다.
    """
    with psycopg.connect(clean_db) as conn:  # DBA가 vector만 미리 깔아주는 기사용자 경로
        conn.execute("CREATE EXTENSION vector")
        conn.commit()

    upgraded = tmp_path / "migrations"
    upgraded.mkdir()
    for path in migration_files():
        (upgraded / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        # 호출마다 새 연결 — 이 파일 하나만 적용하는 업그레이드 세션이다.
        assert asyncio.run(run_migrations(non_superuser_dsn, upgraded)) == [path.name]

    assert applied_migrations(non_superuser_dsn) == [path.name for path in migration_files()]


SCHEMA_ROLE = "cli_schema_role"


def tables_in(dsn: str, schema: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)
        ).fetchall()
    return {row[0] for row in rows}


@pytest.fixture
def org_db(clean_db: str):
    """조직이 이미 쓰는 DB — `--schema`가 겨냥하는 도입 형태다 (#95 C, #84).

    public에 OpenArchive와 같은 이름의 남의 테이블(`documents`·`users`)이 있고, DBA는
    `vector`를 public에 미리 깔아 두었다. 앱 전용 롤은 슈퍼유저가 아니며 DB에 스키마를
    만들 권한(DB CREATE)만 받았다. public에는 CREATE 권한이 없다.
    """
    params = conninfo_to_dict(clean_db)
    dbname = params["dbname"]
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector")
        conn.execute("CREATE TABLE documents (id int PRIMARY KEY, note text)")
        conn.execute("INSERT INTO documents VALUES (1, '조직 문서')")
        conn.execute("CREATE TABLE users (id int PRIMARY KEY, name text)")
        conn.execute(f"DROP ROLE IF EXISTS {SCHEMA_ROLE}")
        conn.execute(f"CREATE ROLE {SCHEMA_ROLE} LOGIN PASSWORD 'schema-role'")
        conn.execute(f'GRANT CONNECT, CREATE ON DATABASE "{dbname}" TO {SCHEMA_ROLE}')
        # vector 타입은 public에 있다. 실제 DB에는 PUBLIC 롤의 기본 USAGE가 있다.
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {SCHEMA_ROLE}")
    try:
        yield make_conninfo(**{**params, "user": SCHEMA_ROLE, "password": "schema-role"})
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute(f"REVOKE ALL ON DATABASE \"{dbname}\" FROM {SCHEMA_ROLE}")
            conn.execute(f"DROP OWNED BY {SCHEMA_ROLE} CASCADE")
            conn.execute(f"DROP ROLE IF EXISTS {SCHEMA_ROLE}")


def test_init_with_schema_installs_beside_existing_tables_without_touching_public(
    org_db: str, clean_db: str, tmp_path
):
    """#95 C — `\\dt public.*`가 그대로여야 한다. 스키마 이름은 접속 롤 이름이다."""
    public_before = tables_in(clean_db, "public")

    exit_code = main(
        ["init", "--dsn", org_db, "--schema", "--yes", "--env-file", str(tmp_path / ".env")]
    )

    assert exit_code == 0
    assert tables_in(clean_db, "public") == public_before
    assert OWNED_TABLES | {"schema_migrations"} <= tables_in(clean_db, SCHEMA_ROLE)
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT note FROM public.documents").fetchall() == [("조직 문서",)]
        (applied,) = conn.execute(
            f"SELECT count(*) FROM {SCHEMA_ROLE}.schema_migrations"
        ).fetchone()
    assert applied == len(migration_files())


def test_a_schema_install_is_what_the_role_sees_without_any_connection_option(
    org_db: str, clean_db: str, tmp_path
):
    """런타임 설정 없이 그 롤의 연결이 곧 전용 스키마를 본다 — 기본 search_path의 `"$user"`.

    GUC(`ALTER ROLE … SET search_path`·DSN `options`)에 기대지 않는 이유는 OpenProxy 실측이다:
    `options`는 조용히 버려졌고, 역할 설정은 풀에 이미 떠 있던 백엔드가 받지 않아 옛
    search_path(public)가 계속 쓰였다 (`OPENSQL_RESEARCH.md` §12-25).
    """
    main(["init", "--dsn", org_db, "--schema", "--yes", "--env-file", str(tmp_path / ".env")])

    document = asyncio.run(_create_as(org_db))

    with psycopg.connect(clean_db) as conn:
        (count,) = conn.execute(
            f"SELECT count(*) FROM {SCHEMA_ROLE}.documents WHERE id = %s", (document["id"],)
        ).fetchone()
        assert conn.execute("SELECT count(*) FROM public.documents").fetchone() == (1,)
    assert count == 1


async def _create_as(dsn: str) -> dict:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        return await create_document(
            conn, filename="note.md", data="전용 스키마".encode(), owner_id="alice"
        )


def test_init_with_schema_is_idempotent(org_db: str, capsys, tmp_path):
    """두 번째 실행은 자기 스키마의 이력을 봐야 한다. public의 이력을 보면 겹치는 이름을
    남의 테이블로 오인해 거부한다."""
    argv = ["init", "--dsn", org_db, "--schema", "--yes", "--env-file", str(tmp_path / ".env")]
    main(argv)
    capsys.readouterr()

    exit_code = main(argv)

    assert exit_code == 0
    assert "이미 최신" in capsys.readouterr().out


def test_init_with_schema_refuses_a_search_path_without_the_role_schema(
    org_db: str, clean_db: str, capsys, tmp_path
):
    """DBA가 롤의 search_path를 `"$user"` 없이 고정해 두면 만든 스키마를 아무도 보지 않는다.

    그대로 진행하면 마이그레이션이 public에 테이블을 만들려 들거나, 적용은 스키마에 하고
    런타임은 public을 읽는 상태가 된다.
    """
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE {SCHEMA_ROLE} SET search_path = public")

    exit_code = main(
        ["init", "--dsn", org_db, "--schema", "--yes", "--env-file", str(tmp_path / ".env")]
    )

    assert exit_code == 1
    assert "search_path" in capsys.readouterr().out
    with psycopg.connect(clean_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (SCHEMA_ROLE,)
        ).fetchone() == (0,)


def test_init_with_schema_refuses_a_role_that_cannot_create_the_schema(
    org_db: str, clean_db: str, capsys, tmp_path
):
    params = conninfo_to_dict(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f'REVOKE CREATE ON DATABASE "{params["dbname"]}" FROM {SCHEMA_ROLE}')

    exit_code = main(
        ["init", "--dsn", org_db, "--schema", "--yes", "--env-file", str(tmp_path / ".env")]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert f"CREATE SCHEMA {SCHEMA_ROLE}" in output
    assert "Traceback" not in output


def test_init_checks_the_schema_it_will_write_into(org_db: str, clean_db: str, capsys, tmp_path):
    """`--schema` 없이도 마이그레이션은 search_path의 첫 스키마에 테이블을 만든다.

    롤 이름의 스키마가 이미 있으면 그곳이다. 충돌 검사가 public만 보면 그 스키마의 남의
    `documents` 위에 009의 ALTER TABLE이 돈다.
    """
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {SCHEMA_ROLE} AUTHORIZATION {SCHEMA_ROLE}")
        conn.execute(f"CREATE TABLE {SCHEMA_ROLE}.documents (id int PRIMARY KEY)")
        conn.execute(f"ALTER TABLE {SCHEMA_ROLE}.documents OWNER TO {SCHEMA_ROLE}")
        # public의 남의 테이블은 치워 둔다 — 이 테스트는 롤 스키마 쪽 판정만 본다.
        conn.execute("DROP TABLE public.documents, public.users")

    exit_code = main(["init", "--dsn", org_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert "documents" in capsys.readouterr().out
    assert "schema_migrations" not in tables_in(clean_db, SCHEMA_ROLE)


def test_init_with_schema_refuses_to_shadow_an_existing_public_install(
    org_db: str, clean_db: str, capsys, tmp_path
):
    """public에 이미 설치된 DB에서 `--schema`를 돌리면 빈 설치가 한 벌 더 생긴다.

    롤 이름 스키마가 생기는 순간 `"$user"`가 먼저 풀려 API·워커·MCP의 모든 연결이 빈
    스키마를 본다 — 기존 문서가 에러 없이 전부 사라진 것처럼 보인다.
    """
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute("DROP TABLE public.documents, public.users")
        conn.execute(f"GRANT CREATE ON SCHEMA public TO {SCHEMA_ROLE}")
    env_file = str(tmp_path / ".env")
    assert main(["init", "--dsn", org_db, "--yes", "--env-file", env_file]) == 0
    capsys.readouterr()

    exit_code = main(["init", "--dsn", org_db, "--schema", "--yes", "--env-file", env_file])

    assert exit_code == 1
    assert "public" in capsys.readouterr().out
    with psycopg.connect(clean_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (SCHEMA_ROLE,)
        ).fetchone() == (0,)


def test_schema_probe_reads_the_role_setting_not_this_backends_search_path(org_db: str, clean_db: str):
    """`ALTER ROLE … SET search_path`는 풀에 이미 떠 있던 백엔드가 받지 않는다 (§12-25).

    init이 그런 옛 백엔드에 붙으면 `current_setting`은 `"$user"`를 보여 통과하지만, 새
    백엔드는 롤 설정을 쓴다. 세션 SET으로 옛 백엔드를 흉내 낸다.
    """
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE {SCHEMA_ROLE} SET search_path = public")

    with psycopg.connect(org_db) as conn:
        conn.execute('SET search_path = "$user", public')
        capabilities = probe_capabilities(conn, own_schema=True)

    assert capabilities.search_path_reaches_schema is False


def test_schema_probe_lets_the_role_setting_override_the_database_setting(
    org_db: str, clean_db: str
):
    """PostgreSQL 우선순위대로 롤 설정이 DB 설정을 이긴다 — 거꾸로 읽으면 멀쩡한 롤을 거부한다."""
    dbname = conninfo_to_dict(clean_db)["dbname"]
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f'ALTER DATABASE "{dbname}" SET search_path = public')
        conn.execute(f"ALTER ROLE {SCHEMA_ROLE} SET search_path = \"$user\", public")
    try:
        with psycopg.connect(org_db) as conn:
            capabilities = probe_capabilities(conn, own_schema=True)
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute(f'ALTER DATABASE "{dbname}" RESET search_path')

    assert capabilities.search_path_reaches_schema is True


def test_init_explains_a_search_path_with_no_existing_schema(
    org_db: str, clean_db: str, capsys, tmp_path
):
    """search_path의 어떤 스키마도 없으면 `current_schema()`가 NULL이다 — 테이블을 만들 자리가 없다."""
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE {SCHEMA_ROLE} SET search_path = no_such_schema")

    exit_code = main(["init", "--dsn", org_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert "no_such_schema" in capsys.readouterr().out


def test_dba_instructions_quote_a_role_name_that_needs_quoting(clean_db: str, capsys, tmp_path):
    """안내문은 DBA가 그대로 복사해 실행한다. 대문자·공백이 든 롤 이름은 따옴표 없이는
    다른 롤을 가리키거나 문법 오류가 난다."""
    role = "Cli Team"
    params = conninfo_to_dict(clean_db)
    with psycopg.connect(clean_db, autocommit=True) as conn:
        conn.execute(f'DROP ROLE IF EXISTS "{role}"')
        conn.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'team'")
        conn.execute(f'GRANT CONNECT ON DATABASE "{params["dbname"]}" TO "{role}"')
    try:
        dsn = make_conninfo(**{**params, "user": role, "password": "team"})
        exit_code = main(
            ["init", "--dsn", dsn, "--schema", "--yes", "--env-file", str(tmp_path / ".env")]
        )
    finally:
        with psycopg.connect(clean_db, autocommit=True) as conn:
            conn.execute(f'REVOKE ALL ON DATABASE "{params["dbname"]}" FROM "{role}"')
            conn.execute(f'DROP ROLE IF EXISTS "{role}"')

    assert exit_code == 1
    assert f'CREATE SCHEMA "{role}" AUTHORIZATION "{role}"' in capsys.readouterr().out


def test_init_asks_for_the_dsn_when_it_is_not_given(clean_db: str, monkeypatch, tmp_path):
    """대화형 경로 — 프롬프트 응답만 바꿔 끼운다."""
    answers = iter([clean_db, "y", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    exit_code = main(["init", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert applied_migrations(clean_db) == [path.name for path in migration_files()]


def test_init_stops_when_the_user_declines_to_apply(clean_db: str, monkeypatch, tmp_path):
    answers = iter([clean_db, "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    exit_code = main(["init", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)


def _no_stdin(_prompt: str) -> str:
    raise EOFError


def test_init_without_dsn_and_stdin_asks_for_the_dsn_option(
    clean_db: str, monkeypatch, capsys, tmp_path
):
    """#79 ① — `--yes`는 예/아니오만 건너뛴다. 비대화형에서 DSN 입력을 만나면 traceback 대신 안내."""
    monkeypatch.setattr("builtins.input", _no_stdin)

    exit_code = main(["init", "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert "--dsn" in capsys.readouterr().out
    assert not (tmp_path / ".env").exists()


def test_init_treats_a_closed_stdin_at_the_apply_prompt_as_declined(
    clean_db: str, monkeypatch, tmp_path
):
    monkeypatch.setattr("builtins.input", _no_stdin)

    exit_code = main(["init", "--dsn", clean_db, "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)


def test_write_dsn_leaves_no_stale_database_url_behind(clean_db: str, tmp_path):
    """dotenv는 뒤에 오는 줄을 채택한다 — 첫 줄만 갈면 옛 값이 이긴다."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://first@localhost:5433/first\n"
        "EMBEDDING_PROVIDER=local\n"
        "DATABASE_URL=postgresql://second@localhost:5433/second\n",
        encoding="utf-8",
    )

    main(["init", "--dsn", clean_db, "--yes", "--env-file", str(env_file)])

    written = env_file.read_text(encoding="utf-8")
    assert written.count("DATABASE_URL=") == 1
    assert f"DATABASE_URL={clean_db}" in written
    assert "EMBEDDING_PROVIDER=local" in written


def test_init_next_steps_point_at_installed_commands_only(clean_db: str, capsys, tmp_path):
    """다음 단계는 설치된 명령만 가리킨다 — 저장소 안 스크립트는 pip 설치본에 없다.

    관리자를 만들지 못했으면 그 자리를 `openarchive create-user`로 안내한다.
    """
    main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    steps = capsys.readouterr().out.split("다음 단계")[1]

    assert "openarchive create-user admin --admin" in steps
    assert "scripts/" not in steps
    # 기동은 `openarchive serve` 하나다 (ADR-041 — 웹 화면은 동봉 빌드). uvicorn·워커·npm을
    # 따로 띄우라는 옛 안내가 남아 있으면 README와 화면이 서로 다른 말을 한다.
    assert "openarchive serve" in steps
    assert "uvicorn" not in steps
    assert "npm" not in steps


def test_init_creates_the_first_admin_from_the_environment(
    clean_db: str, monkeypatch, capsys, tmp_path
):
    """#95 A4 — 자체 가입이 없으므로(ADR-028) 설치가 첫 관리자까지 만든다."""
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrap-secret")

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert accounts(clean_db) == [("admin", True)]
    with psycopg.connect(clean_db) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert verify_password("bootstrap-secret", stored)
    out = capsys.readouterr().out
    assert "bootstrap-secret" not in out
    # 만들었으면 다음 단계에 계정 생성이 다시 나오지 않는다.
    assert "create-user" not in out.split("다음 단계")[1]


def test_init_names_the_first_admin_with_an_option(clean_db: str, monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrap-secret")

    main(
        [
            "init", "--dsn", clean_db, "--yes", "--admin-username", "root",
            "--env-file", str(tmp_path / ".env"),
        ]
    )

    assert accounts(clean_db) == [("root", True)]


def test_init_asks_for_the_admin_password_when_it_is_not_in_the_environment(
    clean_db: str, monkeypatch, tmp_path
):
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "typed-secret")

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert accounts(clean_db) == [("admin", True)]


@pytest.mark.parametrize("answer", ["", None], ids=["empty", "closed-stdin"])
def test_init_without_a_password_finishes_without_an_admin(
    clean_db: str, monkeypatch, capsys, tmp_path, answer
):
    """비밀번호가 없으면 계정을 만들지 않는다 — 빈 비밀번호 계정은 누구나 들어온다.

    스키마는 이미 적용됐으므로 설치 자체는 성공으로 끝내고, 계정 생성을 다음 단계로 넘긴다.
    """
    if answer is not None:
        monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: answer)

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert accounts(clean_db) == []
    assert "openarchive create-user admin --admin" in capsys.readouterr().out


def test_init_leaves_an_existing_admin_alone(migrated_db: str, monkeypatch, capsys, tmp_path):
    """두 번째 init은 관리자를 또 만들지 않고, 비밀번호도 묻지 않는다."""
    _insert_user(migrated_db, "boss", "original", is_admin=True)
    monkeypatch.setenv("ADMIN_PASSWORD", "another-secret")

    exit_code = main(["init", "--dsn", migrated_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert accounts(migrated_db) == [("boss", True)]
    assert "create-user" not in capsys.readouterr().out.split("다음 단계")[1]


def test_init_reports_an_admin_name_taken_by_a_regular_account(
    migrated_db: str, monkeypatch, capsys, tmp_path
):
    """관리자가 없는데 그 이름을 일반 계정이 쓰고 있으면 덮어쓰지 않는다."""
    _insert_user(migrated_db, "admin", "original")
    monkeypatch.setenv("ADMIN_PASSWORD", "another-secret")

    exit_code = main(["init", "--dsn", migrated_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert accounts(migrated_db) == [("admin", False)]
    assert "--admin-username" in capsys.readouterr().out


def test_create_user_makes_a_regular_account_from_the_environment(
    migrated_db: str, monkeypatch
):
    """`scripts/create_admin.py`를 대신한다 — 설치본에는 저장소 스크립트가 없다."""
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-secret")

    exit_code = main(["create-user", "alice", "--dsn", migrated_db])

    assert exit_code == 0
    assert accounts(migrated_db) == [("alice", False)]
    with psycopg.connect(migrated_db) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert verify_password("environment-secret", stored)


def test_create_user_grants_admin_with_the_flag(migrated_db: str, monkeypatch):
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "typed-secret")

    exit_code = main(["create-user", "root", "--admin", "--dsn", migrated_db])

    assert exit_code == 0
    assert accounts(migrated_db) == [("root", True)]


def test_create_user_refuses_to_overwrite_an_existing_username(
    migrated_db: str, monkeypatch, capsys
):
    _insert_user(migrated_db, "alice", "first-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "replacement-secret")

    exit_code = main(["create-user", "alice", "--dsn", migrated_db])

    assert exit_code == 1
    assert "이미 존재" in capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert verify_password("first-secret", stored)


@pytest.mark.parametrize("answer", ["", None], ids=["empty", "closed-stdin"])
def test_create_user_refuses_an_empty_password(migrated_db: str, monkeypatch, answer):
    if answer is not None:
        monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: answer)

    exit_code = main(["create-user", "alice", "--dsn", migrated_db])

    assert exit_code != 0
    assert accounts(migrated_db) == []


def test_create_user_reports_a_connection_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-secret")

    exit_code = main(
        ["create-user", "alice", "--dsn", "postgresql://nobody@127.0.0.1:1/none"]
    )

    assert exit_code == 1
    assert "연결하지 못했습니다" in capsys.readouterr().out


def _insert_user(dsn: str, username: str, password: str, *, is_admin: bool = False) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)"
            " RETURNING id",
            (username, hash_password(password), is_admin),
        ).fetchone()[0]


def test_reset_password_lets_a_locked_out_user_log_in_again(
    migrated_db: str, monkeypatch, capsys
):
    """분실 복구 경로. 현재 비밀번호를 모르는 채로 갈아끼운다."""
    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "recovered")

    exit_code = main(["reset-password", "alice", "--dsn", migrated_db])

    assert exit_code == 0
    with psycopg.connect(migrated_db) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'alice'"
        ).fetchone()[0]
    assert verify_password("recovered", stored)
    assert not verify_password("forgotten", stored)
    # 재설정한 비밀번호를 화면에 되비추지 않는다 — 셸 스크롤백에 평문이 남는다.
    assert "recovered" not in capsys.readouterr().out


def test_reset_password_invalidates_the_sessions_of_that_user(migrated_db: str, monkeypatch):
    user_id = _insert_user(migrated_db, "alice", "forgotten")
    other_id = _insert_user(migrated_db, "bob", "bob secret")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        for token, owner in (("alice-session", user_id), ("bob-session", other_id)):
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at) "
                "VALUES (%s, %s, now() + interval '1 hour')",
                (token, owner),
            )
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "recovered")

    main(["reset-password", "alice", "--dsn", migrated_db])

    with psycopg.connect(migrated_db) as conn:
        remaining = conn.execute("SELECT token FROM sessions").fetchall()
    assert remaining == [("bob-session",)]


def test_reset_password_reports_an_unknown_user_without_changing_anything(
    migrated_db: str, monkeypatch, capsys
):
    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "recovered")

    exit_code = main(["reset-password", "nobody", "--dsn", migrated_db])

    assert exit_code == 1
    assert "nobody" in capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (1,)


def test_reset_password_refuses_an_empty_password(migrated_db: str, monkeypatch, capsys):
    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "")

    exit_code = main(["reset-password", "alice", "--dsn", migrated_db])

    assert exit_code == 2
    with psycopg.connect(migrated_db) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'alice'"
        ).fetchone()[0]
    assert verify_password("forgotten", stored)
    assert capsys.readouterr().out.strip() != ""


def test_reset_password_reports_a_connection_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "recovered")

    exit_code = main(
        ["reset-password", "alice", "--dsn", "postgresql://nobody@127.0.0.1:1/none"]
    )

    assert exit_code == 1
    assert "Traceback" not in capsys.readouterr().out


def test_reset_password_does_not_report_a_query_failure_as_a_connection_failure(
    clean_db: str, monkeypatch, capsys
):
    """연결은 되지만 스키마가 없는 DB. 넓은 except가 이 실패를 "연결하지 못했습니다"로 가렸다."""
    monkeypatch.setattr("openarchive.cli.getpass.getpass", lambda _prompt: "recovered")

    with pytest.raises(psycopg.Error):
        main(["reset-password", "alice", "--dsn", clean_db])

    assert "연결하지 못했습니다" not in capsys.readouterr().out


def ready_documents(dsn: str, count: int) -> list:
    """관계 잡까지 끝난 ready 문서 — 먼저 들어온 문서는 나중 문서를 못 본 상태다."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        ids = []
        for _ in range(count):
            doc_id = insert_document(conn)
            mark_document_ready(conn, doc_id, ["text"], vectors=[unit_vector(0)])
            ids.append(doc_id)
        conn.execute("UPDATE embedding_jobs SET status = 'done'")
    return ids


@pytest.mark.timeout(30)
def test_background_worker_surfaces_a_worker_crash(migrated_db, monkeypatch):
    """헬퍼의 워커가 죽으면 기다리던 명령은 끝나지 않는다 — 타임아웃 뒤 원인 예외가 보여야 한다."""

    async def crash(*_args, **_kwargs):
        raise RuntimeError("worker crashed")

    monkeypatch.setattr("conftest.process_once", crash)

    with pytest.raises(RuntimeError, match="worker crashed"), background_worker(migrated_db):
        time.sleep(0.2)


@pytest.mark.timeout(60)
def test_rebuild_edges_queues_edge_jobs_and_waits_for_the_worker(migrated_db, capsys):
    """판정은 워커가 한다 — 명령은 관계 잡을 걸고 워커가 비울 때까지 기다린다 (#156)."""
    first, second = ready_documents(migrated_db, 2)

    with background_worker(migrated_db):
        assert main(["rebuild-edges", "--dsn", migrated_db]) == 0

    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT dst_document_id FROM document_edges WHERE src_document_id = %s", (first,)
        ).fetchall() == [(second,)]
    output = capsys.readouterr().out
    assert "관계 잡을 걸었습니다: 문서 2건" in output
    assert "2/2" in output
    assert "관계를 다시 계산했습니다: 문서 2건" in output


@pytest.mark.timeout(60)
def test_rebuild_edges_explains_the_wait_while_no_worker_takes_the_jobs(
    migrated_db, monkeypatch, capsys
):
    """워커가 없으면 잡이 줄지 않는다 — 멈춘 것처럼 보이지 않게 기다리는 이유를 알린다."""
    monkeypatch.setattr("openarchive.cli.EDGE_POLL_SECONDS", 0.01)
    monkeypatch.setattr("openarchive.cli.EDGE_STALL_SECONDS", 0.05)
    ready_documents(migrated_db, 1)

    with background_worker(migrated_db, start_after=1.0):
        assert main(["rebuild-edges", "--dsn", migrated_db]) == 0

    output = capsys.readouterr().out
    assert "openarchive serve" in output
    assert "관계를 다시 계산했습니다: 문서 1건" in output


def test_rebuild_edges_stops_waiting_on_ctrl_c_and_leaves_the_jobs(
    migrated_db, monkeypatch, capsys
):
    ready_documents(migrated_db, 1)

    async def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("openarchive.cli.wait_for_edge_jobs", interrupted)

    assert main(["rebuild-edges", "--dsn", migrated_db]) == 130
    assert "워커가 처리합니다" in capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE kind = 'edges' AND status = 'pending'"
        ).fetchone() == (1,)


def test_rebuild_edges_reports_isolated_documents_as_a_failure(migrated_db, monkeypatch, capsys):
    ready_documents(migrated_db, 1)

    async def one_isolated(*_args, **_kwargs):
        return 1

    monkeypatch.setattr("openarchive.cli.wait_for_edge_jobs", one_isolated)

    assert main(["rebuild-edges", "--dsn", migrated_db]) == 1
    assert "관계 판정이 격리된 문서 1건" in capsys.readouterr().out


def test_rebuild_edges_reports_a_connection_failure_without_traceback(capsys):
    assert main([
        "rebuild-edges", "--dsn", "postgresql://nobody@127.0.0.1:1/none"
    ]) == 1
    output = capsys.readouterr()
    assert "연결하지 못했습니다" in output.out
    assert "Traceback" not in output.out + output.err


def test_rebuild_edges_does_not_report_a_query_failure_as_a_connection_failure(clean_db, capsys):
    with pytest.raises(psycopg.Error):
        main(["rebuild-edges", "--dsn", clean_db])
    assert "연결하지 못했습니다" not in capsys.readouterr().out


# ── openarchive reextract ─────────────────────────────────────────────────


def upload_original(dsn: str, text: str) -> str:
    async def _upload():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            document = await create_document(
                conn, filename="note.txt", data=text.encode("utf-8"), owner_id="alice"
            )
            return str(document["id"])

    return asyncio.run(_upload())


def edit_text(dsn: str, document_id: str, content: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            UPDATE documents SET version = version + 1, content = %s, content_hash = %s
             WHERE id = %s
            """,
            (content, hashlib.sha256(content.encode()).hexdigest(), document_id),
        )


def test_reextract_one_document_reports_changed_then_unchanged(migrated_db, capsys):
    document_id = upload_original(migrated_db, "original")
    edit_text(migrated_db, document_id, "edited")

    assert main(["reextract", document_id, "--dsn", migrated_db]) == 0
    assert "바뀜 1건 · 같음 0건 · 실패 0건" in capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT version, content FROM documents WHERE id = %s", (document_id,)
        ).fetchone() == (3, "original")

    assert main(["reextract", document_id, "--dsn", migrated_db]) == 0
    assert "바뀜 0건 · 같음 1건 · 실패 0건" in capsys.readouterr().out


def test_reextract_one_document_without_original_exits_1(migrated_db, capsys):
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        document_id = insert_document(conn)

    assert main(["reextract", str(document_id), "--dsn", migrated_db]) == 1
    assert "원본 파일이 없는 문서" in capsys.readouterr().out


def test_reextract_all_reports_totals_and_exits_1_on_any_failure(migrated_db, capsys):
    changed = upload_original(migrated_db, "original")
    edit_text(migrated_db, changed, "edited")
    broken = upload_original(migrated_db, "broken")
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE document_files SET data = %s WHERE document_id = %s",
            (b" \t\r\n\f", broken),
        )

    assert main(["reextract", "--all", "--dsn", migrated_db]) == 1
    output = capsys.readouterr().out
    assert "재임베딩과 관계 재계산이 뒤따릅니다" in output
    assert "바뀜 1건 · 같음 0건 · 실패 1건" in output
    assert f"실패 {broken}:" in output


def test_reextract_all_without_failures_exits_0(migrated_db, capsys):
    upload_original(migrated_db, "original")

    assert main(["reextract", "--all", "--dsn", migrated_db]) == 0
    assert "바뀜 0건 · 같음 1건 · 실패 0건" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["reextract"],
        ["reextract", "00000000-0000-0000-0000-000000000000", "--all"],
    ],
)
def test_reextract_requires_exactly_one_target(argv):
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2


def test_reextract_reports_a_connection_failure_without_traceback(capsys):
    assert main([
        "reextract", "--all", "--dsn", "postgresql://nobody@127.0.0.1:1/none"
    ]) == 1
    output = capsys.readouterr()
    assert "연결하지 못했습니다" in output.out
    assert "Traceback" not in output.out + output.err


def test_reextract_reports_documents_handed_to_ocr(migrated_db, capsys):
    document_id = upload_original(migrated_db, "original")
    scan = (Path(__file__).parent / "fixtures" / "scan_tax_page1.jpg").read_bytes()
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE document_files SET filename = 'scan.jpg', data = %s WHERE document_id = %s",
            (scan, document_id),
        )

    assert main(["reextract", document_id, "--dsn", migrated_db]) == 0
    output = capsys.readouterr().out
    assert "바뀜 0건 · 같음 0건 · 실패 0건" in output
    assert "텍스트 인식 대기 1건" in output
