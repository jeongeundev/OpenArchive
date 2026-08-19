"""`openarchive init` — 설치 CLI (ADR-039).

실제 pgvector 컨테이너에 붙는다. 이 CLI가 지켜야 하는 것 — 확장 가용성 판정,
기존 스키마와의 충돌 감지, 마이그레이션 적용의 멱등성 — 은 전부 DB가 결정하므로
Mock으로는 확인할 수 없다 (CLAUDE.md 개발 프로세스).
"""

import psycopg
import pytest

from app.cli import OWNED_TABLES, main


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


def test_init_applies_every_migration_to_a_clean_database(clean_db: str, capsys, tmp_path):
    exit_code = main(["init", "--dsn", clean_db, "--yes", "--env-file", str(tmp_path / ".env")])

    assert exit_code == 0
    assert len(applied_migrations(clean_db)) == 13
    assert "documents" in table_names(clean_db)


def test_init_is_idempotent_on_an_already_prepared_database(
    migrated_db: str, capsys, tmp_path
):
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
    from app.cli import probe_capabilities

    with psycopg.connect(clean_db) as conn:
        capabilities = probe_capabilities(conn)

    assert capabilities.extensions[extension] is True
    assert capabilities.server_version_num >= 130000
    assert capabilities.can_create is True
