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
import signal
import subprocess
import sys
import time
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

# 001과 005가 요구한다. 설치 여부만이 아니라 **이 롤이 만들 수 있는지**까지 본다 —
# 아직 없는 확장은 마이그레이션이 만들지만, 배포판에 아예 없거나 롤에 권한이 없으면
# 적용 도중에 실패한다.
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
    creatable_extensions: frozenset[str]
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
               has_schema_privilege(current_user, 'public', 'CREATE'),
               current_setting('is_superuser') = 'on',
               -- 반대로 **확장**을 만들 권한은 데이터베이스가 정하는 자리다. trusted
               -- 확장은 이 권한만으로 만들 수 있고, untrusted 확장은 이것으로도 안 된다.
               has_database_privilege(current_user, current_database(), 'CREATE')
        """
    ).fetchone()
    server_version_num, is_superuser, creates_in_database = row[1], row[5], row[6]
    # `trusted` 컬럼은 PostgreSQL 13에서 생겼다. 그 아래 버전은 어차피 거부되므로
    # 조회하지 않는다 — 하면 UndefinedColumn으로 죽어, "13 이상이 필요합니다"라는
    # 안내가 나갈 자리에 traceback이 나간다.
    extension_rows = (
        conn.execute(
            """
            SELECT available.name,
                   available.installed_version IS NOT NULL,
                   versions.trusted
              FROM pg_available_extensions available
              JOIN pg_available_extension_versions versions
                ON versions.name = available.name
               AND versions.version = available.default_version
             WHERE available.name = ANY(%s)
            """,
            (list(REQUIRED_EXTENSIONS),),
        ).fetchall()
        if server_version_num >= MINIMUM_SERVER_VERSION_NUM
        else []
    )
    available = {name for name, _, _ in extension_rows}
    return Capabilities(
        server_version=row[0],
        server_version_num=server_version_num,
        database=row[2],
        username=row[3],
        extensions={name: name in available for name in REQUIRED_EXTENSIONS},
        installed_extensions=frozenset(
            name for name, is_installed, _ in extension_rows if is_installed
        ),
        # CREATE EXTENSION의 실제 규칙이다 (로컬 컨테이너 실측): 슈퍼유저는 무엇이든
        # 만들고, 비슈퍼유저는 trusted 확장만 그것도 DB CREATE 권한이 있을 때 만든다.
        creatable_extensions=frozenset(
            name
            for name, _, trusted in extension_rows
            if is_superuser or (trusted and creates_in_database)
        ),
        can_create=row[4],
    )


def _unmet_requirements(capabilities: Capabilities) -> list[str]:
    if capabilities.server_version_num < MINIMUM_SERVER_VERSION_NUM:
        # 버전이 미달이면 나머지 판정은 의미가 없다. probe도 확장을 조회하지 않는다.
        return [
            (
                f"PostgreSQL {capabilities.server_version} — 13 이상이 필요합니다 "
                "(gen_random_uuid()를 코어에서 씁니다)"
            )
        ]
    unmet = []
    for name, available in capabilities.extensions.items():
        if not available:
            unmet.append(f"확장 '{name}'을 이 서버에서 설치할 수 없습니다")
        elif (
            name not in capabilities.installed_extensions
            and name not in capabilities.creatable_extensions
        ):
            # 이미 설치돼 있으면 만들 권한은 필요 없다 — 001은 IF NOT EXISTS로 넘어가고,
            # 005처럼 가드 없는 파일은 _blocking_extensions가 따로 잡는다.
            unmet.append(
                f"'{capabilities.username}'에게 확장 '{name}' 생성 권한이 없습니다 "
                "— 슈퍼유저로 실행하거나, DBA에게 미리 설치를 요청하십시오 "
                f"(CREATE EXTENSION {name};)"
            )
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


# Ctrl-C 뒤 자식이 스스로 정리할 시간. 워커는 처리 중인 잡을 마치고 멈춘다 (ADR-004).
SHUTDOWN_GRACE_SECONDS = 10.0


def _serve_processes(host: str, port: int) -> list[tuple[str, list[str]]]:
    """함께 띄울 프로세스. 같은 인터프리터로 부르므로 가상환경·sys.path가 그대로 이어진다."""
    return [
        (
            "API",
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)],
        ),
        ("워커", [sys.executable, "-m", "app.worker"]),
    ]


def _all_stopped(running: list[tuple[str, subprocess.Popen]]) -> bool:
    return all(process.poll() is not None for _name, process in running)


def _first_stopped(running: list[tuple[str, subprocess.Popen]]) -> tuple[str, int] | None:
    """먼저 멈춘 프로세스와 그 종료 코드. 하나라도 멈추면 나머지를 내리는 판정 기준이다."""
    for name, process in running:
        code = process.poll()
        if code is not None:
            return name, code
    return None


def _stop(running: list[tuple[str, subprocess.Popen]], *, already_signalled: bool) -> None:
    """살아 있는 자식을 내린다.

    Ctrl-C는 프로세스 그룹 전체에 가므로 자식은 이미 SIGINT를 받았다. 그 경우 먼저
    스스로 정리할 시간을 준다 — 곧바로 terminate를 겹쳐 보내면 워커가 처리 중인 잡을
    마치지 못한다. 그 시간이 지나도 남아 있거나, 애초에 신호를 받지 않은 경우에만
    SIGTERM을, 그래도 남으면 SIGKILL을 보낸다.
    """
    if already_signalled:
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        while time.monotonic() < deadline and not _all_stopped(running):
            time.sleep(0.1)
        if _all_stopped(running):
            return
    for _name, process in running:
        if process.poll() is None:
            process.terminate()
    for _name, process in running:
        try:
            process.wait(timeout=SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_serve(*, host: str, port: int) -> int:
    """API 서버와 임베딩 워커를 함께 띄운다 (ADR-039 결정 2 개정).

    묶는 것은 **기동과 종료뿐**이다. 죽은 프로세스를 되살리지 않는다 — 재시작은
    systemd의 일이고(ADR-038), 여기서 되살리기 시작하면 프로세스 매니저를 새로 만드는
    별개 문제가 된다. 한쪽이 멈추면 나머지도 내린다: 워커 없이 API만 남으면 업로드가
    성공한 뒤 검색에 잡히지 않아, 아무 에러 없이 조용히 안 되는 상태가 된다.
    """
    # 자식 프로세스와 같은 stdout을 쓴다. 파이프로 나갈 때 블록 버퍼링이 걸리면 이
    # 안내가 통째로 맨 끝으로 밀려 안내 구실을 못 한다.
    sys.stdout.reconfigure(line_buffering=True)
    print("OpenArchive 실행")
    print(f"  API   http://{host}:{port}")
    print("  워커  임베딩 잡 처리")
    print("  Ctrl-C로 둘 다 멈춥니다.")
    print()

    # Ctrl-C(SIGINT)는 터미널이 프로세스 그룹 전체에 보내므로 KeyboardInterrupt만으로
    # 충분하지만, `kill <pid>`·컨테이너 진입점·감독자의 stop은 부모 하나에만 SIGTERM을
    # 보낸다. 그것을 무시하면 부모만 죽고 uvicorn과 워커가 고아로 남아 포트를 쥔 채
    # 잡을 계속 집어간다 (실측).
    def _on_sigterm(_signum, _frame):
        raise _Terminated

    signal.signal(signal.SIGTERM, _on_sigterm)

    running: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in _serve_processes(host, port):
            running.append((name, subprocess.Popen(command)))
    except OSError as error:
        print(f"프로세스를 띄우지 못했습니다: {error}")
        _stop(running, already_signalled=False)
        return 1

    try:
        while (stopped := _first_stopped(running)) is None:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print()
        print("멈추는 중입니다...")
        _stop(running, already_signalled=True)
        return 0
    except _Terminated:
        print()
        print("멈추는 중입니다...")
        # 그룹째 SIGTERM이 온 경우(systemd 기본)에는 자식이 한 번 더 받지만, 둘 다
        # 두 번째 신호를 종료 요청의 반복으로 다루므로 정리를 건너뛰지 않는다.
        _stop(running, already_signalled=False)
        return 0

    name, code = stopped
    print()
    print(f"{name}가 종료됐습니다 (코드 {code}). 나머지도 함께 내립니다.")
    _stop(running, already_signalled=False)
    return code or 1


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
    print("  1) 관리자    ADMIN_PASSWORD='<비밀번호>' python scripts/create_admin.py admin --admin")
    print("  2) 실행      EMBEDDING_PROVIDER=local openarchive serve    (API + 워커 + 웹 화면)")
    return 0


class _Terminated(Exception):
    """부모만 SIGTERM을 받았다. 자식은 신호를 받지 않았으므로 직접 내려야 한다."""


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
    serve = subcommands.add_parser(
        "serve", help="API 서버와 임베딩 워커를 함께 실행합니다."
    )
    serve.add_argument("--host", default="127.0.0.1", help="API가 바인드할 주소 (기본: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="API 포트 (기본: 8000)")
    reset = subcommands.add_parser(
        "reset-password", help="비밀번호를 잊은 계정의 비밀번호를 재설정합니다."
    )
    reset.add_argument("username")
    reset.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return run_serve(host=args.host, port=args.port)
    if args.command == "reset-password":
        return run_reset_password(dsn=args.dsn, username=args.username)
    return run_init(dsn=args.dsn, assume_yes=args.yes, env_file=args.env_file)


if __name__ == "__main__":
    sys.exit(main())
