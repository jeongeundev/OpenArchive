"""`openarchive init` — 설치 CLI (ADR-039).

실제 pgvector 컨테이너에 붙는다. 이 CLI가 지켜야 하는 것 — 확장 가용성 판정,
기존 스키마와의 충돌 감지, 마이그레이션 적용의 멱등성 — 은 전부 DB가 결정하므로
Mock으로는 확인할 수 없다 (CLAUDE.md 개발 프로세스).
"""

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.cli import OWNED_TABLES, main, probe_capabilities
from app.config import ENV_FILE
from app.migrations import migration_files


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


def test_owned_tables_match_the_migration_files():
    """보호 판정의 기준이 되는 목록이므로 마이그레이션과 어긋나면 안 된다.

    새 테이블을 추가하고 이 목록이 따라오지 않으면, 그 이름을 쓰는 남의 테이블을
    감지하지 못한 채 마이그레이션이 ALTER로 손대게 된다.
    """
    assert OWNED_TABLES == {
        "api_tokens",
        "document_chunks",
        "document_edges",
        "document_links",
        "document_versions",
        "documents",
        "embedding_jobs",
        "sessions",
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


def test_init_refuses_when_a_guarded_extension_is_already_installed(
    clean_db: str, capsys, tmp_path
):
    """005는 IF NOT EXISTS 없이 CREATE EXTENSION pg_trgm을 실행한다 (ADR-005 관례).

    DBA가 미리 깔아둔 DB에서는 001~004가 적용된 뒤 005가 duplicate_object로 죽어,
    "확인이 적용보다 먼저"라는 계약이 깨지고 부분 적용 스키마가 남는다.
    """
    with psycopg.connect(clean_db) as conn:
        conn.execute("CREATE EXTENSION pg_trgm")
        conn.commit()

    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 1
    assert "pg_trgm" in capsys.readouterr().out
    # 아무것도 적용하지 않았어야 한다 — 부분 적용이 이 검사의 존재 이유다.
    with psycopg.connect(clean_db) as conn:
        assert conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone() == (None,)


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


def test_init_next_steps_name_the_directory_they_are_relative_to(
    clean_db: str, capsys, tmp_path
):
    """다음 단계의 경로는 기준 디렉토리가 함께 적혀야 하고, 그 기준에서 실재해야 한다.

    init은 실행 위치를 가리지 않는다 — venv의 실행 파일이고 --env-file 기본값도
    절대경로다. 안내가 어느 디렉토리를 전제하는지 밝히지 않으면, README 절차대로
    backend/에서 init을 돌린 사용자가 `python scripts/create_admin.py`를 그 자리에서
    쳐서 파일을 찾지 못한다 (clean clone 실측).
    """
    main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    steps = capsys.readouterr().out.split("다음 단계")[1]
    repo_root = ENV_FILE.parent.parent

    assert "저장소 루트" in steps
    for relative in ("backend", "frontend", "scripts/create_admin.py"):
        assert relative in steps
        assert (repo_root / relative).exists()


def _insert_user(dsn: str, username: str, password: str) -> str:
    from app.services.auth import hash_password

    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
            (username, hash_password(password)),
        ).fetchone()[0]


def test_reset_password_lets_a_locked_out_user_log_in_again(
    migrated_db: str, monkeypatch, capsys
):
    """분실 복구 경로. 현재 비밀번호를 모르는 채로 갈아끼운다."""
    from app.services.auth import verify_password

    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "recovered")

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
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "recovered")

    main(["reset-password", "alice", "--dsn", migrated_db])

    with psycopg.connect(migrated_db) as conn:
        remaining = conn.execute("SELECT token FROM sessions").fetchall()
    assert remaining == [("bob-session",)]


def test_reset_password_reports_an_unknown_user_without_changing_anything(
    migrated_db: str, monkeypatch, capsys
):
    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "recovered")

    exit_code = main(["reset-password", "nobody", "--dsn", migrated_db])

    assert exit_code == 1
    assert "nobody" in capsys.readouterr().out
    with psycopg.connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (1,)


def test_reset_password_refuses_an_empty_password(migrated_db: str, monkeypatch, capsys):
    from app.services.auth import verify_password

    _insert_user(migrated_db, "alice", "forgotten")
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "")

    exit_code = main(["reset-password", "alice", "--dsn", migrated_db])

    assert exit_code == 2
    with psycopg.connect(migrated_db) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'alice'"
        ).fetchone()[0]
    assert verify_password("forgotten", stored)
    assert capsys.readouterr().out.strip() != ""


def test_reset_password_reports_a_connection_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr("app.cli.getpass.getpass", lambda _prompt: "recovered")

    exit_code = main(
        ["reset-password", "alice", "--dsn", "postgresql://nobody@127.0.0.1:1/none"]
    )

    assert exit_code == 1
    assert "Traceback" not in capsys.readouterr().out
