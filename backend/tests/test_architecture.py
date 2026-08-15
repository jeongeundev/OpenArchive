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
    REPOSITORY_ROOT / "backend" / "app",
    REPOSITORY_ROOT / "backend" / "mcp_server",
    REPOSITORY_ROOT / "scripts",
)
# 셸도 검사한다 — scripts/ 는 psql 힙독으로 SQL을 담는다.
SOURCE_SUFFIXES = {".py", ".sh"}
HTTP_MODULES = {"fastapi", "starlette"}
FORBIDDEN_EXAMPLE_MODULES = {"app", "backend", "mcp_server"}


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
    mcp_root = REPOSITORY_ROOT / "backend" / "mcp_server"
    for path in mcp_root.rglob("*.py"):
        if ".execute(" in path.read_text():
            violations.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert not violations, "MCP 서버 SQL 직접 실행 금지 위반:\n" + "\n".join(violations)


def test_services_do_not_import_http_frameworks():
    violations = []
    services_root = REPOSITORY_ROOT / "backend" / "app" / "services"
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
