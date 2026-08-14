import ast
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DERIVED_TABLE_INSERT = re.compile(
    r"\binsert\s+into\s+(embedding_jobs|document_versions|document_edges|document_links)\b",
    re.IGNORECASE,
)
APPLICATION_PYTHON_ROOTS = (
    REPOSITORY_ROOT / "backend" / "app",
    REPOSITORY_ROOT / "backend" / "mcp_server",
    REPOSITORY_ROOT / "scripts",
)
HTTP_MODULES = {"fastapi", "starlette"}


def test_application_code_does_not_insert_into_derived_tables():
    violations = []
    for root in APPLICATION_PYTHON_ROOTS:
        for path in root.rglob("*.py"):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if DERIVED_TABLE_INSERT.search(line):
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{line_number}")

    assert not violations, "파생 테이블 직접 INSERT 금지 위반:\n" + "\n".join(violations)


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
