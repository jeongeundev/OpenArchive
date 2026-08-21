"""`openarchive` 명령 — 설치와 계정 복구를 담당하는 운영자 CLI (ADR-039·ADR-040).

Web UI·REST·MCP와 같은 자리의 인터페이스이며, 로직을 새로 쓰지 않고 코어를 재사용한다.
마이그레이션 적용은 `app.migrations.run_migrations`, 준비 상태 판정은
`app.services.system.get_system_status`가 그대로 한다.

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색, 문서 공급. init은 DB를 준비된
상태로 만들고 다음 단계를 안내하는 데서 끝난다.

`reset-password`는 비밀번호를 잊은 계정의 유일한 탈출구다. 웹에는 두지 않는다 — 남의
비밀번호를 바꾸는 권한을 만들면 is_admin이 계정 관리를 넘어 문서 열람으로 번진다
(ADR-027·ADR-040). 서버 셸 접근자는 이미 DB를 만질 수 있으므로 권한이 늘지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.config import ENV_FILE, get_settings
from app.migrations import (
    APPLIED_SQL,
    HISTORY_TABLE_SQL,
    MIGRATIONS_DIR,
    migration_files,
    pending_filenames,
    run_migrations,
)
from app.services.auth import UserNotFound, reset_password
from app.services.system import get_system_status

# gen_random_uuid()가 코어에 들어온 버전. 그 아래에서는 002가 기동하지 못한다.
MINIMUM_SERVER_VERSION_NUM = 130000

# 001과 005가 요구한다. 설치 여부가 아니라 **설치 가능 여부**를 본다 — 아직 없는
# 확장은 마이그레이션이 만들지만, 배포판에 아예 없으면 그때 가서 실패한다.
REQUIRED_EXTENSIONS = ("vector", "pg_trgm")

_CREATE_TABLE_RE = re.compile(
    r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE | re.MULTILINE,
)

# `IF NOT EXISTS` 없이 만드는 확장만 잡는다. 그런 문장은 확장이 이미 있으면 duplicate_object로
# 죽고, ADR-005 관례상 마이그레이션은 멱등성을 schema_migrations에 맡겨 가드를 쓰지 않는다.
_UNGUARDED_EXTENSION_RE = re.compile(
    r'^\s*CREATE\s+EXTENSION\s+(?!IF\s+NOT\s+EXISTS)"?([A-Za-z_][A-Za-z0-9_]*)',
    re.IGNORECASE | re.MULTILINE,
)


def _owned_tables(migrations_dir: Path = MIGRATIONS_DIR) -> frozenset[str]:
    """마이그레이션이 만드는 테이블 이름. 충돌 판정의 기준이다.

    파일에서 뽑는 이유는 목록이 마이그레이션과 갈라지지 않게 하기 위함이다. 손으로
    적어두면 새 테이블이 추가될 때 조용히 낡고, 그 이름을 쓰는 남의 테이블을 놓친다.
    """
    names: set[str] = set()
    for path in migration_files(migrations_dir):
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
    installed_extensions: frozenset[str]
    can_create: bool


def probe_capabilities(conn: psycopg.Connection) -> Capabilities:
    """붙은 DB가 OpenArchive 스키마를 받을 수 있는지 조회한다. 아무것도 바꾸지 않는다."""
    row = conn.execute(
        """
        SELECT current_setting('server_version'),
               current_setting('server_version_num')::int,
               current_database(),
               current_user,
               -- 테이블을 만들 수 있는지는 데이터베이스가 아니라 **스키마** 권한이 정한다.
               -- has_database_privilege(..., 'CREATE')는 "DB 안에 스키마를 만들 권한"이라,
               -- public에만 CREATE를 받은 롤에서 false가 되어 멀쩡한 DB를 거부한다.
               has_schema_privilege(current_user, 'public', 'CREATE')
        """
    ).fetchone()
    extension_rows = conn.execute(
        "SELECT name, installed_version IS NOT NULL "
        "FROM pg_available_extensions WHERE name = ANY(%s)",
        (list(REQUIRED_EXTENSIONS),),
    ).fetchall()
    available = {name for name, _ in extension_rows}
    installed = {name for name, is_installed in extension_rows if is_installed}
    return Capabilities(
        server_version=row[0],
        server_version_num=row[1],
        database=row[2],
        username=row[3],
        extensions={name: name in available for name in REQUIRED_EXTENSIONS},
        installed_extensions=frozenset(installed),
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
            f"'{capabilities.username}'에게 '{capabilities.database}'의 public 스키마에 대한 "
            "CREATE 권한이 없습니다 (GRANT CREATE ON SCHEMA public TO ...)"
        )
    return unmet


def _conflicting_tables(conn: psycopg.Connection) -> list[str]:
    """이미 있는 테이블 중 OpenArchive가 쓰는 이름. `schema_migrations`가 있으면 우리 것이다.

    마이그레이션 009는 `ALTER TABLE documents`를, 012는 `DELETE FROM document_links`를
    실행한다. 같은 이름의 남의 테이블 위에 적용하면 그 데이터가 손상된다.
    """
    if _has_history_table(conn):
        return []
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (sorted(OWNED_TABLES),),
    ).fetchall()
    return sorted(name for (name,) in rows)


def _has_history_table(conn: psycopg.Connection) -> bool:
    return conn.execute(HISTORY_TABLE_SQL).fetchone()[0] is not None


def _pending_migrations(conn: psycopg.Connection) -> list[str]:
    if not _has_history_table(conn):
        return pending_filenames(set())
    applied = {name for (name,) in conn.execute(APPLIED_SQL).fetchall()}
    return pending_filenames(applied)


def _blocking_extensions(pending: list[str], installed: frozenset[str]) -> dict[str, str]:
    """이미 설치돼 있어 미적용 마이그레이션을 실패시킬 확장 → 그 마이그레이션 파일명.

    적용을 시작한 뒤 중간 파일에서 죽으면 부분 적용 스키마가 남는다. "확인이 적용보다
    먼저"라는 계약이 지켜지려면 이것을 미리 잡아야 한다.
    """
    blocking: dict[str, str] = {}
    for path in migration_files():
        if path.name not in pending:
            continue
        for name in _UNGUARDED_EXTENSION_RE.findall(path.read_text("utf-8")):
            if name in installed:
                blocking.setdefault(name, path.name)
    return blocking


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
    kept: list[str] = []
    replaced = False
    for existing in env_file.read_text(encoding="utf-8").splitlines():
        if not existing.strip().startswith("DATABASE_URL="):
            kept.append(existing)
            continue
        # 첫 줄만 갈고 나머지를 두면 뒤에 남은 옛 값이 이긴다.
        if not replaced:
            kept.append(line)
            replaced = True
    if not replaced:
        kept.append(line)
    env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def _inspect(conn: psycopg.Connection) -> list[str] | None:
    """설치할 수 있는 DB인지 확인하고 미적용 마이그레이션을 낸다. 막히면 None.

    아무것도 바꾸지 않는다 — 이 단계가 적용보다 먼저 오는 것이 init의 존재 이유다.
    """
    capabilities = probe_capabilities(conn)
    print(f"  연결됨 — PostgreSQL {capabilities.server_version}")
    print(f"  데이터베이스 {capabilities.database} · 사용자 {capabilities.username}")

    unmet = _unmet_requirements(capabilities)
    if unmet:
        print()
        print("이 데이터베이스에는 설치할 수 없습니다:")
        for item in unmet:
            print(f"  - {item}")
        return None
    for name in capabilities.extensions:
        state = "이미 설치됨" if name in capabilities.installed_extensions else "사용 가능"
        print(f"  확장 {name} — {state}")

    conflicts = _conflicting_tables(conn)
    if conflicts:
        print()
        print("이미 다른 용도로 쓰이는 데이터베이스로 보입니다. 아무것도 바꾸지 않았습니다.")
        print(f"  OpenArchive가 쓰는 이름과 겹치는 테이블: {', '.join(conflicts)}")
        print("  빈 데이터베이스를 새로 만들어 다시 실행하십시오.")
        return None

    pending = _pending_migrations(conn)
    blocking = _blocking_extensions(pending, capabilities.installed_extensions)
    if blocking:
        print()
        print("확장이 이미 설치돼 있어 마이그레이션이 중간에 실패합니다. 아무것도 바꾸지 않았습니다.")
        for name, filename in sorted(blocking.items()):
            print(f"  - {filename}은 '{name}'을 IF NOT EXISTS 없이 만듭니다")
        print("  DROP EXTENSION으로 걷어내거나, 빈 데이터베이스를 새로 만들어 다시 실행하십시오.")
        return None
    return pending


def run_init(*, dsn: str | None, assume_yes: bool, env_file: Path) -> int:
    print("OpenArchive 설치 준비")
    print()
    if dsn is None:
        print("DB 연결 정보를 입력하세요. OpenProxy 경유라면 데이터베이스 자리에 pool 이름을 적습니다.")
        dsn = _ask("DATABASE_URL", get_settings().database_url)
        print()

    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error as error:
        # 연결 실패만 여기서 잡는다. 더 넓게 감싸면 조회·판정 단계의 실패까지
        # "연결하지 못했습니다"로 보고되어 원인을 가린다.
        print(f"연결하지 못했습니다: {str(error).strip()}")
        return 1

    with connection as conn:
        pending = _inspect(conn)
        if pending is None:
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
    print("다음 단계 — 저장소 루트에서 실행합니다. 이 명령은 프로세스를 기동하지 않습니다.")
    print("  1) API      cd backend && uvicorn app.main:app --reload")
    print("  2) 관리자    ADMIN_PASSWORD='<비밀번호>' python scripts/create_admin.py admin --admin")
    print("  3) 워커      cd backend && python -m app.worker")
    print("  4) 프론트    cd frontend && npm install && npm run dev")
    return 0


class _ConnectionFailed(Exception):
    """DSN으로 붙지 못했다. 붙은 뒤의 실패와 구분해 보고하려고 따로 둔다."""


async def _reset(dsn: str, username: str, new_password: str) -> None:
    """해시 교체와 세션 무효화를 한 트랜잭션에 담는다. 둘 사이에서 끊기면 안 된다.

    연결 실패만 여기서 잡는다 — `run_init`과 같은 이유다. 더 넓게 감싸면 UPDATE·DELETE의
    실패까지 "연결하지 못했습니다"로 보고되어 원인을 가린다.
    """
    try:
        connection = await psycopg.AsyncConnection.connect(dsn, connect_timeout=5)
    except psycopg.Error as error:
        raise _ConnectionFailed(str(error).strip()) from error
    async with connection as conn:
        await reset_password(conn, username, new_password)


def run_reset_password(*, dsn: str | None, username: str) -> int:
    """비밀번호를 잊은 계정을 다시 열어준다. 확인 절차 없이 갈아끼운다."""
    dsn = dsn or get_settings().database_url
    password = getpass.getpass(f"'{username}'의 새 비밀번호: ")
    if not password:
        print("비밀번호가 비어 있어 아무것도 바꾸지 않았습니다.")
        return 2
    try:
        asyncio.run(_reset(dsn, username, password))
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except UserNotFound:
        print(f"'{username}' 계정이 없습니다. 아무것도 바꾸지 않았습니다.")
        return 1
    print(f"'{username}'의 비밀번호를 재설정하고 그 계정의 로그인 세션을 모두 끊었습니다.")
    print("발급된 API 토큰은 그대로 유효합니다 — 폐기는 계정 설정 화면에서 합니다.")
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
    reset = subcommands.add_parser(
        "reset-password", help="비밀번호를 잊은 계정의 비밀번호를 재설정합니다."
    )
    reset.add_argument("username")
    reset.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    args = parser.parse_args(argv)
    if args.command == "reset-password":
        return run_reset_password(dsn=args.dsn, username=args.username)
    return run_init(dsn=args.dsn, assume_yes=args.yes, env_file=args.env_file)


if __name__ == "__main__":
    sys.exit(main())
