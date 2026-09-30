"""`openarchive` 명령 — 설치와 계정 복구를 담당하는 운영자 CLI (ADR-039·ADR-040).

Web UI·REST·MCP와 같은 자리의 인터페이스이며, 로직을 새로 쓰지 않고 코어를 재사용한다.
마이그레이션 적용은 `app.migrations.run_migrations`, 준비 상태 판정은
`app.services.system.get_system_status`가 그대로 한다.

**하지 않는 것**: API·워커·프론트 기동, DB 자동 탐색, 문서 공급. init은 DB를 준비된
상태로 만들고 첫 관리자를 만든 뒤 다음 단계를 안내하는 데서 끝난다. 기동은 `serve`,
문서를 넣고 빼고 찾는 일은 `import`·`export`·`search`가 따로 맡는다(ADR-039 결정 2 개정).

`import`·`export`·`search`는 `--user`로 받은 계정의 권한으로 동작한다. 셸 접근자는 이미
DB를 만질 수 있으므로 비밀번호를 받지 않는다 — `reset-password`와 같은 이유다. 대신 계정이
실제로 있는지는 확인한다. 없는 이름으로 넣은 문서는 아무도 로그인해 볼 수 없다.

첫 관리자를 init이 만드는 이유는 자체 가입이 없어서다(ADR-028) — 계정을 만들어 줄 사람이
있어야 설치가 끝난다. 웹의 "첫 가입자가 관리자" 방식은 설치 직후 URL에 먼저 닿은 사람이
관리자를 차지하므로 두지 않는다. `create-user`는 그 뒤 셸에서 계정을 더 만드는 경로다.

`reset-password`는 비밀번호를 잊은 계정의 유일한 탈출구다. 웹에는 두지 않는다 — 남의
비밀번호를 바꾸는 권한을 만들면 is_admin이 계정 관리를 넘어 문서 열람으로 번진다
(ADR-027·ADR-040). 서버 셸 접근자는 이미 DB를 만질 수 있으므로 권한이 늘지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
import yaml

from app.config import ENV_FILE, get_settings
from app.embeddings import get_provider
from app.migrations import (
    APPLIED_SQL,
    HISTORY_TABLE_SQL,
    MIGRATIONS_DIR,
    migration_files,
    pending_filenames,
    run_migrations,
)
from app.services.auth import (
    UserAlreadyExists,
    UserNotFound,
    admin_exists,
    create_user,
    list_users,
    reset_password,
)
from app.services.documents import (
    DocumentNotFound,
    EmptyExtractedText,
    ExtractedTextTooLarge,
    InvalidVisibility,
    OriginalFileMissing,
    create_document,
    create_text_document,
    find_same_original,
    find_same_text,
    get_document,
    list_documents,
)
from app.services.parsing import (
    SUPPORTED_CONTENT_TYPES,
    UnsupportedFileType,
    detect_content_type,
)
from app.services.search import MAX_K, SearchHit, search_documents
from app.services.system import (
    ReextractSummary,
    get_system_status,
    rebuild_all_edges,
    reextract_all,
    reextract_one,
)
from app.services.visibility import VISIBILITY_VALUES

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
    # 마이그레이션이 테이블을 만들 스키마. 충돌·이력·권한 판정이 모두 이곳을 본다.
    schema: str | None
    schema_exists: bool
    search_path: str
    search_path_reaches_schema: bool


def probe_capabilities(conn: psycopg.Connection, *, own_schema: bool = False) -> Capabilities:
    """붙은 DB가 OpenArchive 스키마를 받을 수 있는지 조회한다. 아무것도 바꾸지 않는다.

    `own_schema`면 대상은 접속 롤과 같은 이름의 스키마다(`init --schema`). 아니면 search_path의
    첫 스키마 — 러너가 실제로 테이블을 만드는 자리다. 둘 다 GUC 없이 기본 search_path의
    `"$user"`로 풀린다. `ALTER ROLE … SET search_path`는 OpenProxy 풀에 이미 떠 있던 백엔드가
    받지 않고, DSN의 `options`는 프록시가 조용히 버린다 (`OPENSQL_RESEARCH.md` §12-25).
    """
    row = conn.execute(
        """
        SELECT current_setting('server_version'),
               current_setting('server_version_num')::int,
               current_database(),
               current_user,
               current_setting('is_superuser') = 'on',
               -- **확장**을 만들 권한은 데이터베이스가 정하는 자리다. trusted 확장은 이
               -- 권한만으로 만들 수 있고, untrusted 확장은 이것으로도 안 된다. 스키마를
               -- 새로 만들 권한도 이것이다.
               has_database_privilege(current_user, current_database(), 'CREATE'),
               current_schema(),
               current_setting('search_path')
        """
    ).fetchone()
    server_version_num, username, is_superuser, creates_in_database = row[1], row[3], row[4], row[5]
    search_path = row[7]
    schema = username if own_schema else row[6]
    (schema_exists,) = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)", (schema,)
    ).fetchone()
    if schema_exists:
        # 테이블을 만들 수 있는지는 데이터베이스가 아니라 **스키마** 권한이 정한다.
        # has_database_privilege(..., 'CREATE')는 "DB 안에 스키마를 만들 권한"이라,
        # public에만 CREATE를 받은 롤에서 false가 되어 멀쩡한 DB를 거부한다.
        (can_create,) = conn.execute(
            "SELECT has_schema_privilege(current_user, %s, 'CREATE')", (schema,)
        ).fetchone()
    else:
        can_create = creates_in_database
    # 대상 스키마가 search_path의 첫 자리로 풀려야 런타임의 모든 연결이 그곳을 본다.
    first_entry = search_path.split(",")[0].strip().strip('"')
    reaches_schema = not own_schema or first_entry in {"$user", username}
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
        username=username,
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
        can_create=can_create,
        schema=schema,
        schema_exists=schema_exists,
        search_path=search_path,
        search_path_reaches_schema=reaches_schema,
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
            # 이미 설치돼 있으면 만들 권한은 필요 없다 — 001·005는 IF NOT EXISTS로 넘어간다.
            unmet.append(
                f"'{capabilities.username}'에게 확장 '{name}' 생성 권한이 없습니다 "
                "— 슈퍼유저로 실행하거나, DBA에게 미리 설치를 요청하십시오 "
                f"(CREATE EXTENSION {name};)"
            )
    user, schema = capabilities.username, capabilities.schema
    if not capabilities.search_path_reaches_schema:
        unmet.append(
            f"'{user}'의 search_path({capabilities.search_path})가 스키마 '{schema}'로 "
            f"시작하지 않습니다 — 롤 설정을 걷어내십시오 (ALTER ROLE {user} RESET search_path)"
        )
    if not capabilities.can_create:
        if capabilities.schema_exists:
            unmet.append(
                f"'{user}'에게 '{capabilities.database}'의 {schema} 스키마에 대한 "
                f"CREATE 권한이 없습니다 (GRANT CREATE ON SCHEMA {schema} TO {user})"
            )
        else:
            unmet.append(
                f"'{user}'에게 '{capabilities.database}'에 스키마를 만들 권한이 없습니다 "
                f"— DBA에게 미리 만들어 달라고 요청하십시오 (CREATE SCHEMA {schema} AUTHORIZATION {user};)"
            )
    return unmet


def _conflicting_tables(conn: psycopg.Connection, schema: str) -> list[str]:
    """이미 있는 테이블 중 OpenArchive가 쓰는 이름. `schema_migrations`가 있으면 우리 것이다.

    마이그레이션 009는 `ALTER TABLE documents`를, 012·015·023은
    `DELETE FROM document_links`를 실행한다. 같은 이름의 남의 테이블 위에 적용하면
    그 데이터가 손상된다.
    """
    if _has_history_table(conn, schema):
        return []
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s AND tablename = ANY(%s)",
        (schema, sorted(OWNED_TABLES)),
    ).fetchall()
    return sorted(name for (name,) in rows)


def _has_history_table(conn: psycopg.Connection, schema: str) -> bool:
    return conn.execute(HISTORY_TABLE_SQL, (schema,)).fetchone()[0] is not None


def _pending_migrations(conn: psycopg.Connection, schema: str) -> list[str]:
    if not _has_history_table(conn, schema):
        return pending_filenames(set())
    applied = {name for (name,) in conn.execute(APPLIED_SQL).fetchall()}
    return pending_filenames(applied)


async def _read_status(dsn: str):
    settings = get_settings()
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        return await get_system_status(
            conn,
            job_lease_seconds=settings.job_lease_seconds,
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
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}
    except EOFError:
        # 입력이 닫힌 비대화형 실행은 기본값(N)으로 본다.
        print()
        return False


def _inspect(conn: psycopg.Connection, *, own_schema: bool) -> list[str] | None:
    """설치할 수 있는 DB인지 확인하고 미적용 마이그레이션을 낸다. 막히면 None.

    아무것도 바꾸지 않는다 — 이 단계가 적용보다 먼저 오는 것이 init의 존재 이유다.
    """
    capabilities = probe_capabilities(conn, own_schema=own_schema)
    print(f"  연결됨 — PostgreSQL {capabilities.server_version}")
    print(f"  데이터베이스 {capabilities.database} · 사용자 {capabilities.username}")
    created = "" if capabilities.schema_exists else " (새로 만듭니다)"
    print(f"  스키마 {capabilities.schema}{created}")

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

    conflicts = _conflicting_tables(conn, capabilities.schema)
    if conflicts:
        print()
        print("이미 다른 용도로 쓰이는 데이터베이스로 보입니다. 아무것도 바꾸지 않았습니다.")
        print(f"  OpenArchive가 쓰는 이름과 겹치는 테이블: {', '.join(conflicts)}")
        print("  빈 데이터베이스를 새로 만들어 다시 실행하십시오.")
        return None

    return _pending_migrations(conn, capabilities.schema)


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
    #
    # 예외로 던지지 않고 플래그만 세운다. 자식을 띄우는 중이나 내리는 중에 신호가 와도
    # 그 자리를 끊지 않으려는 것이다 — 끊으면 방금 뜬 자식을 놓치거나 정리를 하다 만다.
    # 감독자가 재촉으로 신호를 한 번 더 보내는 경우가 후자다.
    terminated = False

    def _on_sigterm(_signum, _frame) -> None:
        nonlocal terminated
        terminated = True

    signal.signal(signal.SIGTERM, _on_sigterm)

    running: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in _serve_processes(host, port):
            if terminated:
                break
            running.append((name, subprocess.Popen(command)))
    except OSError as error:
        print(f"프로세스를 띄우지 못했습니다: {error}")
        _stop(running, already_signalled=False)
        return 1

    stopped: tuple[str, int] | None = None
    try:
        while not terminated and (stopped := _first_stopped(running)) is None:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print()
        print("멈추는 중입니다...")
        _stop(running, already_signalled=True)
        return 0

    if stopped is None:
        # 루프를 빠져나오는 다른 길은 SIGTERM뿐이다. 그룹째 온 경우(systemd 기본)에는
        # 자식이 한 번 더 받지만, uvicorn도 워커도 두 번째 신호를 종료 요청의 반복으로
        # 다루므로 정리를 건너뛰지 않는다.
        print()
        print("멈추는 중입니다...")
        _stop(running, already_signalled=False)
        return 0

    name, code = stopped
    print()
    print(f"{name}가 종료됐습니다 (코드 {code}). 나머지도 함께 내립니다.")
    _stop(running, already_signalled=False)
    return code or 1


def _account_password(prompt: str) -> str:
    """`ADMIN_PASSWORD`, 없으면 프롬프트. 입력이 닫혀 있으면 빈 문자열.

    환경변수는 스크립트·컨테이너 같은 비대화형 설치용이다. 인자로 받지 않는 이유는
    셸 이력과 `ps`에 평문이 남기 때문이다.
    """
    password = os.environ.get("ADMIN_PASSWORD")
    if password:
        return password
    try:
        return getpass.getpass(prompt)
    except EOFError:
        print()
        return ""


async def _has_admin(dsn: str) -> bool:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        return await admin_exists(conn)


async def _create_account(dsn: str, username: str, password: str, *, is_admin: bool) -> None:
    """해시·중복 판정은 서비스가 한다 — 여기 복제하면 두 벌이 되어 갈린다."""
    async with await _connect(dsn, autocommit=True) as conn:
        await create_user(conn, username, password, is_admin=is_admin)


def _ensure_admin(dsn: str, username: str) -> bool | None:
    """관리자가 있으면 True, 비밀번호가 없어 건너뛰면 False, 이름이 막혀 멈추면 None.

    이미 관리자가 있으면 묻지도 않는다 — init은 다시 실행하는 명령이다.
    """
    print()
    if asyncio.run(_has_admin(dsn)):
        print("관리자 계정이 이미 있습니다.")
        return True
    password = _account_password(f"첫 관리자 '{username}'의 비밀번호 (비우면 건너뜁니다): ")
    if not password:
        # 빈 비밀번호 계정은 만들지 않는다. 스키마는 이미 적용됐으므로 설치는 성공으로
        # 끝내고 계정 생성을 다음 단계로 넘긴다.
        print("비밀번호가 없어 관리자 계정을 만들지 않았습니다.")
        return False
    try:
        asyncio.run(_create_account(dsn, username, password, is_admin=True))
    except UserAlreadyExists:
        print(f"'{username}'은 이미 일반 계정이 쓰고 있어 관리자로 만들지 않았습니다.")
        print("  --admin-username으로 다른 이름을 주어 다시 실행하십시오.")
        return None
    print(f"관리자 '{username}'을 만들었습니다.")
    return True


def run_init(
    *,
    dsn: str | None,
    assume_yes: bool,
    env_file: Path,
    admin_username: str = "admin",
    own_schema: bool = False,
) -> int:
    print("OpenArchive 설치 준비")
    print()
    if dsn is None:
        print("DB 연결 정보를 입력하세요. OpenProxy 경유라면 데이터베이스 자리에 pool 이름을 적습니다.")
        try:
            dsn = _ask("DATABASE_URL", get_settings().database_url)
        except EOFError:
            print()
            print("DSN이 필요합니다 — --dsn으로 주거나 대화형으로 실행하십시오.")
            return 1
        print()

    try:
        connection = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.Error as error:
        # 연결 실패만 여기서 잡는다. 더 넓게 감싸면 조회·판정 단계의 실패까지
        # "연결하지 못했습니다"로 보고되어 원인을 가린다.
        print(f"연결하지 못했습니다: {str(error).strip()}")
        return 1

    with connection as conn:
        pending = _inspect(conn, own_schema=own_schema)
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
        if own_schema:
            # 이름을 생략하면 롤 이름이 스키마 이름이 된다. 만든 순간부터 그 롤의 모든 연결이
            # 기본 search_path의 "$user"로 이곳을 본다 — 풀에 떠 있던 백엔드까지 (§12-25).
            with psycopg.connect(dsn) as conn:
                conn.execute("CREATE SCHEMA IF NOT EXISTS AUTHORIZATION CURRENT_USER")
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
    print(f"  관계가 아직 계산되지 않은 문서 {status.stale_edge_documents}건")

    has_admin = _ensure_admin(dsn, admin_username)
    if has_admin is None:
        return 1

    if assume_yes or _confirm(f"이 DSN을 {env_file}에 저장할까요?"):
        _write_dsn(env_file, dsn)
        print(f"  {env_file}에 DATABASE_URL을 기록했습니다")

    print()
    print("다음 단계 — 이 명령은 프로세스를 기동하지 않습니다.")
    # 설치된 명령만 안내한다. 저장소 안 스크립트는 pip 설치본에 없다.
    if not has_admin:
        print(f"  관리자    openarchive create-user {admin_username} --admin")
    print("  실행      EMBEDDING_PROVIDER=local openarchive serve    (API + 워커 + 웹 화면)")
    return 0


class _ConnectionFailed(Exception):
    """DSN으로 붙지 못했다. 붙은 뒤의 실패와 구분해 보고하려고 따로 둔다."""


async def _connect(dsn: str, **kwargs) -> psycopg.AsyncConnection:
    """연결 실패만 `_ConnectionFailed`로 바꾼다 — `run_init`과 같은 이유다. 더 넓게 감싸면
    연결 뒤 UPDATE·DELETE·재계산의 실패까지 "연결하지 못했습니다"로 보고되어 원인을 가린다.
    """
    try:
        return await psycopg.AsyncConnection.connect(dsn, connect_timeout=5, **kwargs)
    except psycopg.Error as error:
        raise _ConnectionFailed(str(error).strip()) from error


async def _reset(dsn: str, username: str, new_password: str) -> None:
    """해시 교체와 세션 무효화를 한 트랜잭션에 담는다. 둘 사이에서 끊기면 안 된다."""
    async with await _connect(dsn) as conn:
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


def run_create_user(*, dsn: str | None, username: str, is_admin: bool) -> int:
    """셸에서 계정을 만든다. 첫 관리자는 init이 만들고, 이것은 그 뒤의 경로다."""
    dsn = dsn or get_settings().database_url
    password = _account_password(f"'{username}'의 비밀번호: ")
    if not password:
        print("비밀번호가 비어 있어 계정을 만들지 않았습니다.")
        return 2
    try:
        asyncio.run(_create_account(dsn, username, password, is_admin=is_admin))
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except UserAlreadyExists as error:
        print(error)
        return 1
    print(f"사용자 '{username}'을 생성했습니다.")
    return 0


def _rebuild_progress(done: int, total: int) -> None:
    print(f"\r  {done}/{total}", end="", flush=True)


async def _rebuild_edges(dsn: str) -> int:
    async with await _connect(dsn, autocommit=True) as conn:
        return await rebuild_all_edges(conn, on_progress=_rebuild_progress)


def run_rebuild_edges(*, dsn: str | None) -> int:
    dsn = dsn or get_settings().database_url
    try:
        count = asyncio.run(_rebuild_edges(dsn))
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    print(f"\n관계를 다시 계산했습니다: 문서 {count}건")
    return 0


async def _reextract_one(dsn: str, document_id: UUID) -> ReextractSummary:
    async with await _connect(dsn, autocommit=True) as conn:
        return await reextract_one(conn, document_id)


async def _reextract_all(dsn: str) -> ReextractSummary:
    async with await _connect(dsn, autocommit=True) as conn:
        return await reextract_all(conn, on_progress=_rebuild_progress)


def run_reextract(*, dsn: str | None, document_id: UUID | None) -> int:
    dsn = dsn or get_settings().database_url
    try:
        if document_id is None:
            print("바뀐 문서마다 재임베딩과 관계 재계산이 뒤따릅니다.")
            summary = asyncio.run(_reextract_all(dsn))
            print()
        else:
            summary = asyncio.run(_reextract_one(dsn, document_id))
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except DocumentNotFound:
        print(f"문서 {document_id}이(가) 없습니다.")
        return 1
    except OriginalFileMissing:
        print(f"원본 파일이 없는 문서는 다시 추출할 수 없습니다: {document_id}")
        return 1
    print(
        f"다시 추출했습니다: 바뀜 {summary.changed}건 · 같음 {summary.unchanged}건"
        f" · 실패 {len(summary.failed)}건"
    )
    for failed_id, reason in summary.failed:
        print(f"  실패 {failed_id}: {reason}")
    if summary.awaiting_ocr:
        print(
            f"텍스트 인식 대기 {summary.awaiting_ocr}건 — 원본이 이미지나 스캔이라"
            " 워커가 텍스트를 인식한 뒤 반영합니다."
        )
    if summary.changed:
        print("바뀐 문서는 새 텍스트 버전이 되었고 워커가 다시 임베딩합니다.")
    return 1 if summary.failed else 0


async def _require_user(conn: psycopg.AsyncConnection, username: str) -> None:
    if username not in {user["username"] for user in await list_users(conn)}:
        raise UserNotFound(username)


# 파일 첫 줄이 `---`인 YAML 블록. export가 쓰는 모양이자 Obsidian 볼트의 관례다.
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)

# 파일 하나의 실패로 보고하고 다음 파일로 넘어가는 예외. 파싱 실패(ValueError)는 업로드
# 라우터가 400으로 옮기는 것과 같은 범위다. OSError는 그 파일을 읽지 못한 것(권한 등)이다.
# 그 밖의 DB 오류는 폴더를 계속 돌 이유가 없다.
_IMPORT_FILE_ERRORS = (
    ValueError, OSError, EmptyExtractedText, ExtractedTextTooLarge, InvalidVisibility
)


@dataclass
class _Frontmatter:
    title: str | None
    tags: list[str]
    visibility: str | None
    body: str


def _read_frontmatter(data: bytes) -> _Frontmatter | None:
    """frontmatter가 있는 마크다운이면 메타데이터와 본문으로 나눈다. 없으면 None.

    frontmatter는 문서 텍스트가 아니라 메타데이터다 — 본문에 남기면 검색·청킹에 YAML이 섞인다.
    title·tags·visibility만 읽고 나머지 키(aliases 등)는 버린다.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        header = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"frontmatter를 읽지 못했습니다: {error.__class__.__name__}") from error
    if not isinstance(header, dict):
        # 호출부 타입 오류가 아니라 파일 내용이 잘못된 것이다 — 파싱 실패와 같은 ValueError로 둬야
        # import가 이 파일만 실패로 보고하고 다음 파일로 넘어간다.
        raise ValueError("frontmatter가 키: 값 형식이 아닙니다.")  # noqa: TRY004
    tags = header.get("tags") or []
    return _Frontmatter(
        title=str(header["title"]) if header.get("title") is not None else None,
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [str(tags)],
        visibility=header.get("visibility"),
        body=text[match.end():],
    )


def _import_candidates(folder: Path) -> list[Path]:
    """하위 폴더까지의 파일. 숨김 파일·폴더(.obsidian, .git)는 문서가 아니므로 뺀다."""
    return [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    ]


@dataclass
class _ImportSummary:
    imported: int = 0
    existing: int = 0
    unsupported: int = 0
    failed: int = 0
    awaiting_ocr: int = 0


async def _import_file(
    conn: psycopg.AsyncConnection,
    path: Path,
    *,
    username: str,
    tags: list[str],
    visibility: str,
) -> dict | None:
    """파일 하나를 문서로 만든다. 이미 있으면 None.

    업로드와 같은 진입점(`create_document`)이라 원본을 보관한다(ADR-046). frontmatter가
    있는 마크다운만 예외로, 본문을 텍스트 진입점으로 넣는다 — 원본 바이트에는 메타데이터가
    섞여 있어 그대로 추출하면 문서 텍스트가 달라진다.
    """
    data = path.read_bytes()
    front = _read_frontmatter(data) if detect_content_type(path.name) == "md" else None
    if front is None:
        if await find_same_original(conn, owner_id=username, data=data):
            return None
        return await create_document(
            conn,
            filename=path.name,
            data=data,
            owner_id=username,
            tags=tags,
            visibility=visibility,
        )
    if await find_same_text(conn, owner_id=username, content=front.body):
        return None
    return await create_text_document(
        conn,
        title=front.title or path.stem,
        content=front.body,
        owner_id=username,
        tags=front.tags + tags,
        visibility=front.visibility or visibility,
    )


async def _import(
    dsn: str, folder: Path, *, username: str, tags: list[str], visibility: str
) -> _ImportSummary:
    limit_mb = get_settings().max_upload_mb
    summary = _ImportSummary()
    async with await _connect(dsn, autocommit=True) as conn:
        await _require_user(conn, username)
        for path in _import_candidates(folder):
            name = path.relative_to(folder).as_posix()
            try:
                detect_content_type(path.name)
            except UnsupportedFileType:
                summary.unsupported += 1
                continue
            try:
                # 업로드와 같은 상한이다 — CLI가 웹보다 큰 파일을 받을 이유가 없다. MB도 업로드처럼
                # 10^6 바이트다(api/documents.py `_read_upload`).
                if path.stat().st_size > limit_mb * 1_000_000:
                    raise ValueError(f"업로드 파일은 {limit_mb}MB를 넘을 수 없습니다.")
                document = await _import_file(
                    conn, path, username=username, tags=tags, visibility=visibility
                )
            except _IMPORT_FILE_ERRORS as error:
                summary.failed += 1
                print(f"  실패 {name}: {error}")
                continue
            if document is None:
                summary.existing += 1
                continue
            summary.imported += 1
            if document["extraction_status"] == "pending":
                summary.awaiting_ocr += 1
    return summary


def run_import(
    *, dsn: str | None, folder: Path, username: str, tags: list[str], visibility: str
) -> int:
    """폴더의 문서를 `username` 소유로 넣는다. 같은 내용이 이미 있으면 건너뛴다."""
    if not folder.is_dir():
        print(f"폴더가 아닙니다: {folder}")
        return 2
    dsn = dsn or get_settings().database_url
    try:
        summary = asyncio.run(
            _import(dsn, folder, username=username, tags=tags, visibility=visibility)
        )
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except UserNotFound:
        print(f"'{username}' 계정이 없습니다. 아무것도 넣지 않았습니다.")
        return 1
    print(
        f"가져옴 {summary.imported}건 · 이미 있음 {summary.existing}건"
        f" · 지원하지 않는 형식 {summary.unsupported}건 · 실패 {summary.failed}건"
    )
    if summary.awaiting_ocr:
        print(
            f"텍스트 인식 대기 {summary.awaiting_ocr}건 — 원본이 이미지나 스캔이라"
            " 워커가 텍스트를 인식한 뒤 문서 텍스트가 채워집니다."
        )
    if summary.imported:
        print("임베딩과 관계 판정은 워커가 합니다 — openarchive serve가 돌고 있어야 검색에 나타납니다.")
        print("많이 넣었다면 워커가 다 처리한 뒤 openarchive rebuild-edges로 관계를 전체 기준으로 맞추세요.")
    return 1 if summary.failed else 0


# 파일 이름에 쓸 수 없거나(`/`, NUL) 운영체제마다 막히는(`:`, `?` 등) 문자.
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _export_filename(title: str, taken: set[str]) -> str:
    """제목에서 파일 이름을 만든다. 겹치면 ` (2)`를 붙인다.

    대소문자만 다른 이름도 겹친 것으로 본다 — macOS·Windows 파일 시스템은 둘을 같은 파일로
    다뤄 뒤의 것이 앞의 것을 덮는다. 앞의 점은 뗀다 — 숨김 파일이 되면 import가 건너뛴다.
    """
    stem = _UNSAFE_FILENAME_RE.sub("_", title).strip().lstrip(".")[:100].strip() or "문서"
    name, counter = f"{stem}.md", 2
    while name.casefold() in taken:
        name, counter = f"{stem} ({counter}).md", counter + 1
    taken.add(name.casefold())
    return name


def _markdown_with_frontmatter(document: dict) -> str:
    header = yaml.safe_dump(
        {
            "title": document["title"],
            "tags": list(document["tags"]),
            "visibility": document["visibility"],
        },
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{header}---\n{document['content']}"


async def _export(dsn: str, folder: Path, *, username: str) -> tuple[int, int]:
    written = skipped = 0
    async with await _connect(dsn, autocommit=True) as conn:
        await _require_user(conn, username)
        # 목록은 열람 범위(공개 + 소유)다. 그중 소유 문서만 내보낸다 — 남의 공개 문서를
        # 다시 넣으면 넣은 사람의 소유가 되어 소유자가 조용히 바뀐다.
        summaries = [
            summary
            for summary in await list_documents(conn, user_id=username)
            if summary["owner_id"] == username
        ]
        folder.mkdir(parents=True, exist_ok=True)
        taken: set[str] = set()
        # 목록은 최신순이다. 오래된 문서부터 이름을 잡아야 제목이 겹칠 때 번호가 매번 같다.
        for summary in reversed(summaries):
            # 인식 중·인식 실패 문서는 문서 텍스트가 비어 있다. 빈 파일은 다시 넣을 때 실패한다.
            if summary["extraction_status"] != "done":
                skipped += 1
                continue
            document = await get_document(conn, summary["id"], user_id=username)
            path = folder / _export_filename(document["title"], taken)
            path.write_text(_markdown_with_frontmatter(document), encoding="utf-8", newline="")
            written += 1
    return written, skipped


def run_export(*, dsn: str | None, folder: Path, username: str) -> int:
    """`username` 소유 문서를 문서 텍스트 + frontmatter(title·tags·visibility) 마크다운으로 쓴다.

    원본 파일은 내보내지 않는다 — 다시 넣으면 파일 문서도 문서 텍스트로 돌아온다.
    """
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        print(f"폴더가 비어 있지 않습니다: {folder} — 덮어쓰지 않으려고 멈췄습니다.")
        return 2
    dsn = dsn or get_settings().database_url
    try:
        written, skipped = asyncio.run(_export(dsn, folder, username=username))
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except UserNotFound:
        print(f"'{username}' 계정이 없습니다.")
        return 1
    print(f"내보냄 {written}건 → {folder}")
    if skipped:
        print(f"텍스트가 없어 건너뜀 {skipped}건 — 텍스트 인식 중이거나 인식에 실패한 문서입니다.")
    return 0


async def _search(
    dsn: str, query: str, *, username: str, tags: list[str], content_type: str | None, k: int
) -> list[SearchHit]:
    async with await _connect(dsn, autocommit=True) as conn:
        await _require_user(conn, username)
        return await search_documents(
            conn,
            get_provider(),
            query=query,
            user_id=username,
            tags=tags,
            content_type=content_type,
            k=k,
        )


def _snippet(text: str, width: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def run_search(
    *,
    dsn: str | None,
    query: str,
    username: str,
    tags: list[str],
    content_type: str | None,
    k: int,
) -> int:
    """`username`이 볼 수 있는 문서에서 찾는다. 웹 검색과 같은 단일 SQL이다(`search_documents`)."""
    if not 1 <= k <= MAX_K:
        print(f"k는 1 이상 {MAX_K} 이하여야 합니다.")
        return 2
    dsn = dsn or get_settings().database_url
    try:
        hits = asyncio.run(
            _search(
                dsn, query, username=username, tags=tags, content_type=content_type, k=k
            )
        )
    except _ConnectionFailed as error:
        print(f"연결하지 못했습니다: {error}")
        return 1
    except UserNotFound:
        print(f"'{username}' 계정이 없습니다.")
        return 1
    if not hits:
        print("결과가 없습니다.")
        return 0
    for rank, hit in enumerate(hits, start=1):
        tag_text = f"  [{', '.join(hit.tags)}]" if hit.tags else ""
        print(f"{rank}. {hit.title}  {hit.score:.3f}{tag_text}")
        print(f"   {_snippet(hit.content)}")
        if hit.via is not None:
            print(f"   관계로 찾음: {hit.via.kind} · {hit.via.depth}단계")
        print(f"   {hit.document_id}")
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
    init.add_argument(
        "--admin-username",
        default="admin",
        help="관리자가 없을 때 만들 첫 관리자 이름 (기본: admin). 비밀번호는 ADMIN_PASSWORD 또는 프롬프트",
    )
    init.add_argument(
        "--schema",
        action="store_true",
        help="접속 롤과 같은 이름의 전용 스키마에 설치합니다. public은 건드리지 않습니다.",
    )
    create = subcommands.add_parser(
        "create-user", help="계정을 만듭니다. 비밀번호는 ADMIN_PASSWORD 또는 프롬프트로 받습니다."
    )
    create.add_argument("username")
    create.add_argument("--admin", action="store_true", help="관리자 권한을 부여합니다.")
    create.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
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
    rebuild_help = (
        "모든 문서의 관계를 전체 코퍼스 기준으로 다시 계산합니다. 대량 적재 뒤 한 번 실행합니다."
    )
    rebuild = subcommands.add_parser(
        "rebuild-edges", help=rebuild_help, description=rebuild_help
    )
    rebuild.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    reextract_help = (
        "보관된 최신 원본에서 텍스트를 다시 추출합니다. 파서를 고친 뒤 기존 문서에 적용합니다."
    )
    reextract = subcommands.add_parser(
        "reextract", help=reextract_help, description=reextract_help
    )
    target = reextract.add_mutually_exclusive_group(required=True)
    target.add_argument("document_id", nargs="?", type=UUID, help="다시 추출할 문서 ID")
    target.add_argument("--all", action="store_true", help="원본이 있는 문서 전부")
    reextract.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    user_help = "이 계정의 권한으로 동작합니다."
    importer = subcommands.add_parser(
        "import", help="폴더의 문서를 하위 폴더까지 넣습니다. 이미 있는 내용은 건너뜁니다."
    )
    importer.add_argument("folder", type=Path)
    importer.add_argument("--user", required=True, help=f"{user_help} 넣은 문서의 소유자가 됩니다.")
    importer.add_argument(
        "--tag", action="append", default=[], help="모든 문서에 붙일 태그. 여러 번 줄 수 있습니다."
    )
    importer.add_argument(
        "--visibility",
        choices=VISIBILITY_VALUES,
        default="public",
        help="frontmatter에 없을 때의 열람 범위 (기본: public)",
    )
    importer.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    exporter = subcommands.add_parser(
        "export", help="소유 문서를 frontmatter 붙은 마크다운으로 내보냅니다."
    )
    exporter.add_argument("folder", type=Path, help="비어 있거나 없는 폴더")
    exporter.add_argument("--user", required=True, help=f"{user_help} 이 계정 소유 문서만 내보냅니다.")
    exporter.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    searcher = subcommands.add_parser(
        "search", help="문서를 검색합니다. 질의 임베딩은 EMBEDDING_PROVIDER를 따릅니다."
    )
    searcher.add_argument("query")
    searcher.add_argument("--user", required=True, help=f"{user_help} 볼 수 있는 문서만 찾습니다.")
    searcher.add_argument("--tag", action="append", default=[], help="이 태그 중 하나가 붙은 문서만")
    searcher.add_argument("--type", choices=SUPPORTED_CONTENT_TYPES, help="이 형식의 문서만")
    searcher.add_argument("-k", type=int, default=10, help=f"결과 수 (1~{MAX_K}, 기본: 10)")
    searcher.add_argument("--dsn", help="DB 연결 문자열. 생략하면 DATABASE_URL을 씁니다.")
    args = parser.parse_args(argv)
    if args.command == "import":
        return run_import(
            dsn=args.dsn,
            folder=args.folder,
            username=args.user,
            tags=args.tag,
            visibility=args.visibility,
        )
    if args.command == "export":
        return run_export(dsn=args.dsn, folder=args.folder, username=args.user)
    if args.command == "search":
        return run_search(
            dsn=args.dsn,
            query=args.query,
            username=args.user,
            tags=args.tag,
            content_type=args.type,
            k=args.k,
        )
    if args.command == "reextract":
        return run_reextract(dsn=args.dsn, document_id=None if args.all else args.document_id)
    if args.command == "rebuild-edges":
        return run_rebuild_edges(dsn=args.dsn)
    if args.command == "serve":
        return run_serve(host=args.host, port=args.port)
    if args.command == "reset-password":
        return run_reset_password(dsn=args.dsn, username=args.username)
    if args.command == "create-user":
        return run_create_user(dsn=args.dsn, username=args.username, is_admin=args.admin)
    return run_init(
        dsn=args.dsn,
        assume_yes=args.yes,
        env_file=args.env_file,
        admin_username=args.admin_username,
        own_schema=args.schema,
    )


if __name__ == "__main__":
    sys.exit(main())
