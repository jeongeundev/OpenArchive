"""마이그레이션 러너 (ADR-005·ADR-012).

실제 pgvector 컨테이너에 붙는다. 이 러너가 검증해야 하는 것 — 트랜잭션 경계,
이력 기록, 실패 시 롤백 — 은 전부 DB가 결정하므로 Mock으로는 확인할 수 없다.

SQL 픽스처는 tmp_path에 직접 쓴다. 실제 `001_extensions.sql`·`002_tables.sql`은
다음 step의 범위이며, 러너는 그것들과 무관하게 검증 가능해야 한다.
"""

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

import app
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


def test_default_migrations_dir_is_inside_the_app_package():
    """마이그레이션은 `app` 패키지 안에 있어야 설치본(site-packages)에서도 찾는다.

    패키지 밖(`backend/migrations/`)에 두면 편집 설치에서만 보이고, `pip install`로 깐
    설치본에서는 `init`과 API startup이 스키마를 찾지 못해 죽는다 (#90-1).
    """
    assert MIGRATIONS_DIR == Path(app.__file__).resolve().parent / "migrations"
    assert sorted(MIGRATIONS_DIR.glob("*.sql"))


def test_the_built_wheel_carries_every_migration(tmp_path: Path):
    """위치만으로는 부족하다 — package-data에 없으면 wheel에서 빠진다.

    작업 트리를 그대로 빌드하면 편집 설치가 남긴 `*.egg-info`의 파일 목록이 새어 들어와,
    package-data에서 SQL을 빼도 wheel에 실린다(실측). 깨끗한 사본에서 빌드한다.
    """
    backend = Path(app.__file__).resolve().parents[1]
    source = tmp_path / "source"
    shutil.copytree(
        backend,
        source,
        ignore=shutil.ignore_patterns(
            ".venv", "*.egg-info", "build", "__pycache__", "tests", ".env"
        ),
    )
    subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", str(source), "--no-deps",
            "--no-build-isolation", "--quiet", "--wheel-dir", str(tmp_path),
        ],
        check=True,
    )
    (wheel,) = tmp_path.glob("*.whl")

    with zipfile.ZipFile(wheel) as archive:
        packed = {name for name in archive.namelist() if name.startswith("app/migrations/")}

    assert packed >= {f"app/migrations/{path.name}" for path in MIGRATIONS_DIR.glob("*.sql")}


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


def test_every_extension_is_created_only_if_missing():
    """확장은 DB 전체에 하나라 우리 이력(`schema_migrations`)이 멱등성을 맡을 수 없다.

    가드 없는 `CREATE EXTENSION`은 DBA가 미리 깔아 둔 DB에서 중간 파일이 duplicate_object로
    죽어 부분 적용 스키마를 남긴다. 조직 DB에 설치하는 경로(`init --schema`)에서 흔한 일이다.
    """
    # 줄 단위로 보면 줄바꿈으로 나뉜 문장(`CREATE\nEXTENSION`)이나 주석 뒤 문장을 놓친다.
    create = re.compile(r"\bCREATE\s+EXTENSION\b(\s+IF\s+NOT\s+EXISTS\b)?", re.IGNORECASE)
    matches = [
        (path.name, match)
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        for match in create.finditer(_without_comments(path.read_text(encoding="utf-8")))
    ]

    assert matches
    assert [name for name, match in matches if match.group(1) is None] == []


def _without_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


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
