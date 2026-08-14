"""마이그레이션 러너 (ADR-005·ADR-012).

실제 pgvector 컨테이너에 붙는다. 이 러너가 검증해야 하는 것 — 트랜잭션 경계,
이력 기록, 실패 시 롤백 — 은 전부 DB가 결정하므로 Mock으로는 확인할 수 없다.

SQL 픽스처는 tmp_path에 직접 쓴다. 실제 `001_extensions.sql`·`002_tables.sql`은
다음 step의 범위이며, 러너는 그것들과 무관하게 검증 가능해야 한다.
"""

from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.config import get_settings
from app.migrations import MIGRATIONS_DIR, run_migrations


@pytest.fixture
def ordered_migrations(tmp_path: Path) -> Path:
    """002가 001의 산출물에 의존한다 — 순서가 뒤집히면 002가 실패한다."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_create.sql").write_text("CREATE TABLE widgets (id int PRIMARY KEY);")
    (d / "002_extend.sql").write_text("ALTER TABLE widgets ADD COLUMN label text;")
    return d


def test_default_migrations_dir_points_at_backend_migrations():
    """기본값이 backend/migrations를 가리켜야 API 기동이 스키마를 찾는다."""
    assert MIGRATIONS_DIR.name == "migrations"
    assert MIGRATIONS_DIR.parent.name == "backend"
    assert MIGRATIONS_DIR.is_dir()


def test_clean_db_targets_the_dedicated_test_database(clean_db: str):
    """스키마를 드롭하는 픽스처가 개발 DB를 가리키면 개발 데이터가 사라진다."""
    with psycopg.connect(clean_db) as conn:
        (current,) = conn.execute("SELECT current_database()").fetchone()

    dev_dbname = conninfo_to_dict(get_settings().database_url)["dbname"]

    # 기대값을 conftest에서 import하지 않고 여기 적는다 — 상수를 공유하면
    # 픽스처가 개발 DB를 가리키도록 바뀌어도 이 테스트가 함께 따라가 버린다.
    # 이름 끝에는 PID가 붙는다(세션마다 다르다). 두 pytest가 같은 DB 서버에서
    # 서로의 테스트 DB를 DROP하지 않게 하려는 것이며, 근거는 conftest 주석에 있다.
    assert current.startswith("openarchive_test_")
    assert current != dev_dbname


def test_clean_db_starts_with_an_empty_schema(clean_db: str):
    with psycopg.connect(clean_db) as conn:
        (tables,) = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()

    assert tables == 0


async def test_applies_files_in_filename_order(clean_db: str, ordered_migrations: Path):
    applied = await run_migrations(clean_db, ordered_migrations)

    assert applied == ["001_create.sql", "002_extend.sql"]

    with psycopg.connect(clean_db) as conn:
        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'widgets' ORDER BY ordinal_position"
        ).fetchall()

    assert [c[0] for c in columns] == ["id", "label"]


async def test_rerun_applies_nothing(clean_db: str, ordered_migrations: Path):
    await run_migrations(clean_db, ordered_migrations)

    again = await run_migrations(clean_db, ordered_migrations)

    assert again == []

    with psycopg.connect(clean_db) as conn:
        (recorded,) = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()

    assert recorded == 2


async def test_records_each_applied_file_with_a_timestamp(
    clean_db: str, ordered_migrations: Path
):
    await run_migrations(clean_db, ordered_migrations)

    with psycopg.connect(clean_db) as conn:
        rows = conn.execute(
            "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"
        ).fetchall()

    assert [r[0] for r in rows] == ["001_create.sql", "002_extend.sql"]
    assert all(r[1] is not None for r in rows)


async def test_a_failing_file_leaves_no_partial_effect(clean_db: str, tmp_path: Path):
    """파일 하나가 트랜잭션 하나여야 한다.

    첫 문장은 유효해서 실행되고, 두 번째에서 실패한다. 앞 문장의 효과가 남으면
    다음 실행이 같은 파일을 처음부터 다시 적용하면서 깨진다.
    """
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_broken.sql").write_text(
        "CREATE TABLE half_applied (id int);\n"
        "ALTER TABLE definitely_missing ADD COLUMN x int;\n"
    )

    with pytest.raises(psycopg.errors.UndefinedTable):
        await run_migrations(clean_db, d)

    with psycopg.connect(clean_db) as conn:
        (leftover,) = conn.execute(
            "SELECT to_regclass('public.half_applied') IS NOT NULL"
        ).fetchone()
        (recorded,) = conn.execute(
            "SELECT count(*) FROM schema_migrations WHERE filename = '001_broken.sql'"
        ).fetchone()

    assert leftover is False
    assert recorded == 0


async def test_a_failing_file_does_not_roll_back_earlier_files(clean_db: str, tmp_path: Path):
    """앞 파일은 이미 커밋됐으므로 남아야 한다 — 다시 돌리면 뒷 파일부터 재개된다."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_create.sql").write_text("CREATE TABLE widgets (id int PRIMARY KEY);")
    (d / "002_broken.sql").write_text("ALTER TABLE definitely_missing ADD COLUMN x int;")

    with pytest.raises(psycopg.errors.UndefinedTable):
        await run_migrations(clean_db, d)

    with psycopg.connect(clean_db) as conn:
        (survived,) = conn.execute("SELECT to_regclass('public.widgets') IS NOT NULL").fetchone()
        recorded = conn.execute("SELECT filename FROM schema_migrations").fetchall()

    assert survived is True
    assert [r[0] for r in recorded] == ["001_create.sql"]


async def test_empty_directory_applies_nothing(clean_db: str, tmp_path: Path):
    d = tmp_path / "migrations"
    d.mkdir()

    assert await run_migrations(clean_db, d) == []


async def test_ignores_non_sql_files_and_subdirectories(clean_db: str, tmp_path: Path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "001_create.sql").write_text("CREATE TABLE widgets (id int PRIMARY KEY);")
    (d / "README.md").write_text("적용 대상이 아니다")
    (d / "archive").mkdir()
    (d / "archive" / "999_old.sql").write_text("SELECT 1/0;")

    applied = await run_migrations(clean_db, d)

    assert applied == ["001_create.sql"]
