import ast
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DERIVED_TABLE_INSERT = re.compile(
    r"\binsert\s+into\s+(embedding_jobs|document_versions|document_edges|document_links)\b",
    re.IGNORECASE,
)
APPLICATION_SOURCE_ROOTS = (
    REPOSITORY_ROOT / "backend" / "openarchive",
    REPOSITORY_ROOT / "scripts",
)
# 셸도 검사한다 — scripts/ 는 psql 힙독으로 SQL을 담는다.
SOURCE_SUFFIXES = {".py", ".sh"}
HTTP_MODULES = {"fastapi", "starlette"}
FORBIDDEN_EXAMPLE_MODULES = {"backend", "openarchive"}


def _example_imports() -> dict[Path, set[str]]:
    imports = {}
    for path in (REPOSITORY_ROOT / "examples").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".", maxsplit=1)[0])
        imports[path] = modules
    assert imports, "examples/ 아래에 검사할 Python 예제가 없습니다."
    return imports


def test_application_code_does_not_insert_into_derived_tables():
    violations = []
    for root in APPLICATION_SOURCE_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            # 공백을 접어서 본다. 줄 단위로 훑으면 테이블명이 다음 줄로 넘어간
            # 여러 줄짜리 SQL 문자열을 통째로 놓친다.
            if DERIVED_TABLE_INSERT.search(" ".join(path.read_text().split())):
                violations.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert not violations, "파생 테이블 직접 INSERT 금지 위반:\n" + "\n".join(violations)


def test_mcp_server_does_not_execute_sql_directly():
    violations = []
    mcp_root = REPOSITORY_ROOT / "backend" / "openarchive" / "mcp_server"
    for path in mcp_root.rglob("*.py"):
        if ".execute(" in path.read_text():
            violations.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert not violations, "MCP 서버 SQL 직접 실행 금지 위반:\n" + "\n".join(violations)


def test_services_do_not_import_http_frameworks():
    violations = []
    services_root = REPOSITORY_ROOT / "backend" / "openarchive" / "services"
    for path in services_root.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            imported_modules = []
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            for imported in imported_modules:
                module = imported.split(".", maxsplit=1)[0]
                if module in HTTP_MODULES:
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno} imports {imported}"
                    )

    assert not violations, "services HTTP 의존 금지 위반:\n" + "\n".join(violations)


def test_examples_do_not_import_application_modules():
    violations = {
        str(path.relative_to(REPOSITORY_ROOT)): sorted(modules & FORBIDDEN_EXAMPLE_MODULES)
        for path, modules in _example_imports().items()
        if modules & FORBIDDEN_EXAMPLE_MODULES
    }

    assert not violations, f"examples 애플리케이션 의존 금지 위반: {violations}"


def test_examples_only_import_standard_library_modules():
    violations = {
        str(path.relative_to(REPOSITORY_ROOT)): sorted(modules - sys.stdlib_module_names)
        for path, modules in _example_imports().items()
        if modules - sys.stdlib_module_names
    }

    assert not violations, f"examples 표준 라이브러리 밖 의존: {violations}"


def test_application_does_not_insert_audit_log():
    pattern = re.compile(r"\binsert\s+into\s+audit_log\b", re.IGNORECASE)
    assert not [str(path) for path in APPLICATION_SOURCE_ROOTS[0].rglob("*.py")
                if pattern.search(path.read_text())]


def test_audit_gucs_are_confined_to_helper():
    root = APPLICATION_SOURCE_ROOTS[0]
    names = ("openarchive.actor_id", "openarchive.actor_via", "openarchive.share_id")
    assert not [str(path) for path in root.rglob("*.py")
                if path != root / "services" / "audit.py"
                and any(name in path.read_text() for name in names)]


def test_application_does_not_set_session_audit_gucs():
    pattern = re.compile(r"\bSET\s+openarchive\.", re.IGNORECASE)
    assert not [str(path) for path in APPLICATION_SOURCE_ROOTS[0].rglob("*.py")
                if pattern.search(path.read_text())]


def test_trash_condition_is_confined_to_visibility_and_trash_service():
    root = APPLICATION_SOURCE_ROOTS[0]
    uses = set()
    for path in root.rglob("*.py"):
        source = path.read_text()
        if path == root / "api" / "schemas.py":
            # 응답 필드 선언만 예외다. SQL 조건이나 다른 사용은 계속 검사한다.
            source = re.sub(r"(?m)^\s*deleted_at:[^\n]*\n", "", source)
        if path == root / "user_cli.py":
            # REST 클라이언트가 휴지통 응답의 필드를 읽는 것만 예외다 (ADR-057). DB 조건이 아니다.
            source = source.replace('["deleted_at"]', "")
        if "deleted_at" in source:
            uses.add(path.relative_to(root).as_posix())
    assert uses <= {"services/visibility.py", "services/trash.py"}, uses


def test_server_side_binding_is_confined_to_original_file_insert():
    """서버 바인딩은 원본 파일 `%b` INSERT 하나만 쓴다 (ADR-062, #210).

    OpenProxy는 파라미터 문장을 서버 명령문으로 쌓고, assert 빌드인 배포판은 쌓인 계획만큼 모든
    문장을 느리게 한다. 이름 붙인 prepare는 26000 사고 경로다(§17).
    """
    root = APPLICATION_SOURCE_ROOTS[0]
    # 서버 바인딩 커서(`Cursor`·`AsyncCursor`·`ServerCursor`·`AsyncServerCursor`), 이름 붙인 서버 커서,
    # 이름 붙인 prepare. 클라이언트 바인딩(`AsyncClientCursor`)은 걸리지 않는다.
    pattern = re.compile(
        r"\b(?:Async)?(?:Server)?Cursor\(|\.cursor\(\s*(?:name\s*=|[\"'])|\bprepare\s*=\s*True"
    )
    uses = {path.relative_to(root).as_posix() for path in root.rglob("*.py")
            if pattern.search(path.read_text())}
    assert uses <= {"services/documents.py"}, uses
    documents = (root / "services" / "documents.py").read_text()
    assert len(pattern.findall(documents)) == 1
