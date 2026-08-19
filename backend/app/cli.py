"""`openarchive` 명령 — 설치를 준비하는 대화형 CLI (ADR-039).

Web UI·REST·MCP와 같은 자리의 인터페이스이며, 로직을 새로 쓰지 않고 코어를 재사용한다.
마이그레이션 적용은 `app.migrations.run_migrations`, 준비 상태 판정은
`app.services.system.get_system_status`가 그대로 한다.

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색, 문서 공급. init은 DB를 준비된
상태로 만들고 다음 단계를 안내하는 데서 끝난다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.config import ENV_FILE, get_settings
from app.migrations import MIGRATIONS_DIR, run_migrations
from app.services.system import get_system_status

# gen_random_uuid()가 코어에 들어온 버전. 그 아래에서는 002가 기동하지 못한다.
MINIMUM_SERVER_VERSION_NUM = 130000

# 001과 005가 요구한다. 설치 여부가 아니라 **설치 가능 여부**를 본다 — 아직 없는
# 확장은 마이그레이션이 만들지만, 배포판에 아예 없으면 그때 가서 실패한다.
REQUIRED_EXTENSIONS = ("vector", "pg_trgm")

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)", re.IGNORECASE | re.MULTILINE
)


def _owned_tables(migrations_dir: Path = MIGRATIONS_DIR) -> frozenset[str]:
    """마이그레이션이 만드는 테이블 이름. 충돌 판정의 기준이다.

    파일에서 뽑는 이유는 목록이 마이그레이션과 갈라지지 않게 하기 위함이다. 손으로
    적어두면 새 테이블이 추가될 때 조용히 낡고, 그 이름을 쓰는 남의 테이블을 놓친다.
    """
    names: set[str] = set()
    for path in sorted(migrations_dir.glob("*.sql")):
        names.update(match.lower() for match in _CREATE_TABLE_RE.findall(path.read_text("utf-8")))
    return frozenset(names)


OWNED_TABLES = _owned_tables()


@dataclass(frozen=True)
class Capabilities:
    server_version: str
    server_version_num: int
    database: str
    username: str
    extensions: dict[str, bool]
    can_create: bool


def probe_capabilities(conn: psycopg.Connection) -> Capabilities:
    """붙은 DB가 OpenArchive 스키마를 받을 수 있는지 조회한다. 아무것도 바꾸지 않는다."""
    row = conn.execute(
        """
        SELECT current_setting('server_version'),
               current_setting('server_version_num')::int,
               current_database(),
               current_user,
               has_database_privilege(current_user, current_database(), 'CREATE')
        """
    ).fetchone()
    available = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM pg_available_extensions WHERE name = ANY(%s)",
            (list(REQUIRED_EXTENSIONS),),
        ).fetchall()
    }
    return Capabilities(
        server_version=row[0],
        server_version_num=row[1],
        database=row[2],
        username=row[3],
        extensions={name: name in available for name in REQUIRED_EXTENSIONS},
        can_create=row[4],
    )


def _unmet_requirements(capabilities: Capabilities) -> list[str]:
    unmet = []
    if capabilities.server_version_num < MINIMUM_SERVER_VERSION_NUM:
        unmet.append(
            f"PostgreSQL {capabilities.server_version} — 13 이상이 필요합니다 "
            "(gen_random_uuid()를 코어에서 씁니다)"
        )
    for name, available in capabilities.extensions.items():
        if not available:
            unmet.append(f"확장 '{name}'을 이 서버에서 설치할 수 없습니다")
    if not capabilities.can_create:
        unmet.append(
            f"'{capabilities.username}'에게 데이터베이스 "
            f"'{capabilities.database}'의 CREATE 권한이 없습니다"
        )
    return unmet


def _conflicting_tables(conn: psycopg.Connection) -> list[str]:
    """이미 있는 테이블 중 OpenArchive가 쓰는 이름. `schema_migrations`가 있으면 우리 것이다.

    마이그레이션 009는 `ALTER TABLE documents`를, 012는 `DELETE FROM document_links`를
    실행한다. 같은 이름의 남의 테이블 위에 적용하면 그 데이터가 손상된다.
    """
    if conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] is not None:
        return []
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (sorted(OWNED_TABLES),),
    ).fetchall()
    return sorted(name for (name,) in rows)


def _pending_migrations(conn: psycopg.Connection) -> list[str]:
    files = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    if conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] is None:
        return files
    done = {
        name for (name,) in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    return [name for name in files if name not in done]


async def _read_status(dsn: str):
    settings = get_settings()
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        return await get_system_status(
            conn,
            zombie_timeout_minutes=settings.zombie_timeout_minutes,
            embedding_provider=settings.embedding_provider,
        )


def _write_dsn(env_file: Path, dsn: str) -> None:
    """`DATABASE_URL` 줄만 갈아 끼운다. .env는 사람이 손으로 관리하는 파일이다."""
    line = f"DATABASE_URL={dsn}"
    if not env_file.exists():
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(line + "\n", encoding="utf-8")
        return
    lines = env_file.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, existing in enumerate(lines):
        if existing.strip().startswith("DATABASE_URL="):
            lines[index] = line
            replaced = True
            break
    if not replaced:
        lines.append(line)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def run_init(*, dsn: str | None, assume_yes: bool, env_file: Path) -> int:
    print("OpenArchive 설치 준비")
    print()
    if dsn is None:
        print("DB 연결 정보를 입력하세요. OpenProxy 경유라면 데이터베이스 자리에 pool 이름을 적습니다.")
        dsn = _ask("DATABASE_URL", get_settings().database_url)
        print()

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            capabilities = probe_capabilities(conn)
            print(f"  연결됨 — PostgreSQL {capabilities.server_version}")
            print(f"  데이터베이스 {capabilities.database} · 사용자 {capabilities.username}")

            unmet = _unmet_requirements(capabilities)
            if unmet:
                print()
                print("이 데이터베이스에는 설치할 수 없습니다:")
                for item in unmet:
                    print(f"  - {item}")
                return 1
            for name, available in capabilities.extensions.items():
                print(f"  확장 {name} — 사용 가능{'' if available else ' 아님'}")

            conflicts = _conflicting_tables(conn)
            if conflicts:
                print()
                print("이미 다른 용도로 쓰이는 데이터베이스로 보입니다. 아무것도 바꾸지 않았습니다.")
                print(f"  OpenArchive가 쓰는 이름과 겹치는 테이블: {', '.join(conflicts)}")
                print("  빈 데이터베이스를 새로 만들어 다시 실행하십시오.")
                return 1

            pending = _pending_migrations(conn)
    except psycopg.Error as error:
        print(f"연결하지 못했습니다: {str(error).strip()}")
        return 1

    print()
    if pending:
        print(f"적용할 마이그레이션 {len(pending)}개:")
        for name in pending:
            print(f"  - {name}")
        if not assume_yes and not _confirm("적용할까요?"):
            print("취소했습니다. 아무것도 바꾸지 않았습니다.")
            return 1
        applied = asyncio.run(run_migrations(dsn))
        print(f"  {len(applied)}개 적용 완료")
    else:
        print("스키마는 이미 최신입니다.")

    status = asyncio.run(_read_status(dsn))
    print()
    print("준비 완료")
    print(f"  노드 {status.node_address}:{status.node_port}")
    print(
        f"  임베딩 잡 — 대기 {status.jobs.pending} · 처리 중 {status.jobs.processing} "
        f"· 실패 {status.jobs.error}"
    )
    print(f"  원본과 어긋난 문서 {status.inconsistent_documents}건")

    if assume_yes or _confirm(f"이 DSN을 {env_file}에 저장할까요?"):
        _write_dsn(env_file, dsn)
        print(f"  {env_file}에 DATABASE_URL을 기록했습니다")

    print()
    print("다음 단계 — 이 명령은 프로세스를 기동하지 않습니다.")
    print("  1) API      cd backend && uvicorn app.main:app --reload")
    print("  2) 관리자    ADMIN_PASSWORD='<비밀번호>' python scripts/create_admin.py admin --admin")
    print("  3) 워커      cd backend && python -m app.worker")
    print("  4) 프론트    cd frontend && npm install && npm run dev")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openarchive", description="OpenArchive 운영 CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="DB를 확인하고 스키마를 준비합니다.")
    init.add_argument("--dsn", help="DB 연결 문자열. 생략하면 대화형으로 묻습니다.")
    init.add_argument("--yes", action="store_true", help="확인 없이 진행합니다.")
    init.add_argument(
        "--env-file", type=Path, default=ENV_FILE, help=f"DSN을 기록할 파일 (기본: {ENV_FILE})"
    )
    args = parser.parse_args(argv)
    if args.command == "init":
        return run_init(dsn=args.dsn, assume_yes=args.yes, env_file=args.env_file)
    parser.error(f"알 수 없는 명령: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
