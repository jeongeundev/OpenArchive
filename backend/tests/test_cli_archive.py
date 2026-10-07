"""`openarchive import`·`export`·`search`·`ask` — 셸에서 문서를 넣고 빼고 찾고 묻는 CLI (#95-b, #96 b).

세 명령 모두 코어(`openarchive.services`)를 그대로 부른다. 여기서 지키는 것은 CLI가 더하는
부분 — 폴더 순회·frontmatter·재실행 건너뛰기·행위 주체 확인·출력 — 이고, 문서 생성과
검색 자체는 실제 DB 위에서 트리거·워커가 만든 결과로 확인한다.
"""

import asyncio
from pathlib import Path

import psycopg
import pytest
import yaml
from conftest import run_embedding_worker

from openarchive.answers import AnswerUnavailable
from openarchive.cli import main
from openarchive.config import get_settings
from openarchive.services.auth import hash_password
from openarchive.services.documents import (
    create_document,
    create_text_document,
    update_extracted_text,
    update_tags,
)

UNREACHABLE_DSN = "postgresql://nobody@127.0.0.1:1/none"


@pytest.fixture
def archive_db(migrated_db: str) -> str:
    """alice·bob 두 계정이 있는 DB. CLI는 계정이 실제로 있는지부터 확인한다."""
    with psycopg.connect(migrated_db) as conn:
        for username in ("alice", "bob"):
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password("test-password")),
            )
    return migrated_db


def documents(dsn: str) -> list[dict]:
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute(
            """
            SELECT d.title, d.filename, d.content, d.owner_id, d.visibility, d.tags,
                   d.extraction_status,
                   (SELECT count(*) FROM document_files f WHERE f.document_id = d.id) AS files
            FROM documents d
            ORDER BY d.title
            """
        )
        return cur.fetchall()


def write(folder: Path, relative: str, content: str | bytes) -> Path:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def frontmatter(path: Path) -> tuple[dict, str]:
    """export가 쓴 파일을 테스트 쪽에서 독립적으로 읽는다 — CLI의 파서로 CLI를 검증하지 않는다."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    header, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(header), body


def seed(dsn: str, *coroutines) -> None:
    async def run() -> None:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            for make in coroutines:
                await make(conn)

    asyncio.run(run())


# ── import ─────────────────────────────────────────────────────────────


def test_import_walks_the_folder_like_an_upload_and_keeps_originals(
    archive_db: str, tmp_path: Path, capsys
):
    """하위 폴더까지 들어가고, 업로드와 같은 진입점이라 원본을 1판으로 보관한다(ADR-046).

    숨김 파일·폴더(.obsidian, .git)는 문서가 아니므로 건드리지 않고, 모르는 형식은 실패가
    아니라 건너뛴 것으로 센다 — 폴더를 통째로 넣는 명령이라 섞여 있는 게 정상이다.
    """
    write(tmp_path, "guide.md", "# 가이드\n\nOpenSQL 설치 안내")
    write(tmp_path, "notes/meeting.txt", "회의록 본문")
    write(tmp_path, ".obsidian/workspace.md", "설정")
    write(tmp_path, "notes/.draft.md", "숨김 초안")
    write(tmp_path, "tool.exe", b"\x00\x01")

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 0
    rows = documents(archive_db)
    assert [(r["title"], r["filename"], r["owner_id"], r["files"]) for r in rows] == [
        ("guide", "guide.md", "alice", 1),
        ("meeting", "meeting.txt", "alice", 1),
    ]
    out = capsys.readouterr().out
    assert "가져옴 2건 · 이미 있음 0건 · 지원하지 않는 형식 1건 · 실패 0건" in out


def test_import_applies_tag_and_visibility_options(archive_db: str, tmp_path: Path):
    write(tmp_path, "a.md", "본문 A")

    exit_code = main(
        [
            "import", str(tmp_path), "--user", "alice", "--dsn", archive_db,
            "--tag", "회의", "--tag", "2026", "--visibility", "private",
        ]
    )

    assert exit_code == 0
    [row] = documents(archive_db)
    assert (row["tags"], row["visibility"]) == (["회의", "2026"], "private")


def test_import_reads_frontmatter_as_metadata_and_stores_only_the_body(
    archive_db: str, tmp_path: Path
):
    """frontmatter는 문서 텍스트가 아니라 메타데이터다 — export 결과와 Obsidian 볼트가 이 모양이다.

    본문만 텍스트 진입점으로 넣으므로 원본 파일이 없다(filename NULL). `--tag`는 더해지고,
    frontmatter에 없는 값만 옵션이 채운다.
    """
    write(
        tmp_path,
        "exported.md",
        "---\ntitle: 운영 원칙\ntags: [운영, 정책]\nvisibility: private\n---\n본문 첫 줄\n\n둘째 문단\n",
    )
    write(tmp_path, "vault.md", "---\ntags: 메모\naliases: [별칭]\n---\n볼트 본문\n")

    exit_code = main(
        ["import", str(tmp_path), "--user", "alice", "--dsn", archive_db, "--tag", "가져옴"]
    )

    assert exit_code == 0
    rows = {r["title"]: r for r in documents(archive_db)}
    assert set(rows) == {"운영 원칙", "vault"}
    exported = rows["운영 원칙"]
    assert (exported["filename"], exported["files"]) == (None, 0)
    assert exported["content"] == "본문 첫 줄\n\n둘째 문단\n"
    assert exported["tags"] == ["운영", "정책", "가져옴"]
    assert exported["visibility"] == "private"
    vault = rows["vault"]
    assert (vault["content"], vault["tags"], vault["visibility"]) == (
        "볼트 본문\n",
        ["메모", "가져옴"],
        "public",
    )


def test_import_again_skips_what_is_already_there(archive_db: str, tmp_path: Path, capsys):
    """중간에 실패해 다시 돌려도 두 벌이 생기지 않는다 — 파일은 원본 바이트, frontmatter는 본문으로 본다."""
    write(tmp_path, "guide.md", "원본 문서")
    write(tmp_path, "exported.md", "---\ntitle: 내보낸 것\n---\n텍스트 문서\n")
    assert main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db]) == 0
    capsys.readouterr()

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 0
    assert len(documents(archive_db)) == 2
    assert "가져옴 0건 · 이미 있음 2건" in capsys.readouterr().out


def test_import_does_not_skip_because_someone_else_has_the_same_file(
    archive_db: str, tmp_path: Path
):
    write(tmp_path, "guide.md", "같은 파일")
    assert main(["import", str(tmp_path), "--user", "bob", "--dsn", archive_db]) == 0

    assert main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db]) == 0

    assert sorted(r["owner_id"] for r in documents(archive_db)) == ["alice", "bob"]


def test_import_reports_failed_files_and_keeps_going(archive_db: str, tmp_path: Path, capsys):
    """한 파일의 실패가 폴더 전체를 멈추지 않는다. 대신 종료 코드로 실패가 있었음을 알린다."""
    write(tmp_path, "blank.txt", "   \n")
    write(tmp_path, "broken.md", "---\ntitle: [닫히지 않음\n---\n본문\n")
    write(tmp_path, "latin1.txt", "caf\xe9".encode("latin-1"))
    write(tmp_path, "good.md", "정상 문서")

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 1
    assert [r["title"] for r in documents(archive_db)] == ["good"]
    out = capsys.readouterr().out
    for name in ("blank.txt", "broken.md", "latin1.txt"):
        assert f"실패 {name}:" in out
    assert "가져옴 1건 · 이미 있음 0건 · 지원하지 않는 형식 0건 · 실패 3건" in out


def test_import_refuses_a_file_over_the_upload_limit(
    archive_db: str, tmp_path: Path, monkeypatch, capsys
):
    """업로드와 같은 상한이다 — CLI가 웹보다 큰 파일을 받을 이유가 없다.

    경계 바로 위(1MB + 1바이트)로 잰다. 업로드의 MB는 10^6 바이트라, MiB로 재면 이 파일이 들어간다.
    이미지라 텍스트 상한(500KB)에 먼저 걸리지 않는다.
    """
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    scan = (Path(__file__).parent / "fixtures" / "scan_tax_page1.jpg").read_bytes()
    write(tmp_path, "big.jpg", scan + b"\0" * (1_000_001 - len(scan)))

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 1
    assert documents(archive_db) == []
    assert "실패 big.jpg: 업로드 파일은 1MB를 넘을 수 없습니다." in capsys.readouterr().out


def test_import_reports_an_unreadable_file_and_keeps_going(
    archive_db: str, tmp_path: Path, capsys
):
    """권한이 없는 파일 하나가 폴더 전체를 트레이스백으로 멈추지 않는다."""
    locked = write(tmp_path, "locked.txt", "잠긴 문서")
    locked.chmod(0)
    write(tmp_path, "good.md", "정상 문서")
    try:
        exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])
    finally:
        locked.chmod(0o644)

    assert exit_code == 1
    assert [r["title"] for r in documents(archive_db)] == ["good"]
    assert "실패 locked.txt:" in capsys.readouterr().out


def test_import_leaves_scans_for_the_worker_and_says_so(archive_db: str, tmp_path: Path, capsys):
    """이미지는 업로드처럼 「추출 중」으로 들어가고 워커가 인식한다(ADR-052) — 그 사실을 알린다."""
    scan = (Path(__file__).parent / "fixtures" / "scan_tax_page1.jpg").read_bytes()
    write(tmp_path, "scan.jpg", scan)

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 0
    [row] = documents(archive_db)
    assert row["extraction_status"] == "pending"
    assert "텍스트 인식 대기 1건" in capsys.readouterr().out


def test_import_refuses_an_unknown_user_before_touching_anything(
    archive_db: str, tmp_path: Path, capsys
):
    """없는 계정 이름으로 넣으면 아무도 로그인해 볼 수 없는 문서가 생긴다."""
    write(tmp_path, "a.md", "본문")

    exit_code = main(["import", str(tmp_path), "--user", "carol", "--dsn", archive_db])

    assert exit_code == 1
    assert documents(archive_db) == []
    assert "'carol' 계정이 없습니다" in capsys.readouterr().out


def test_import_refuses_a_path_that_is_not_a_folder(archive_db: str, tmp_path: Path, capsys):
    exit_code = main(
        ["import", str(tmp_path / "missing"), "--user", "alice", "--dsn", archive_db]
    )

    assert exit_code == 2
    assert "폴더가 아닙니다" in capsys.readouterr().out


def test_import_reports_a_connection_failure_without_traceback(tmp_path: Path, capsys):
    write(tmp_path, "a.md", "본문")

    exit_code = main(["import", str(tmp_path), "--user", "alice", "--dsn", UNREACHABLE_DSN])

    assert exit_code == 1
    assert "연결하지 못했습니다" in capsys.readouterr().out


# ── import --keep-folders · --grant-group (#187 d) ─────────────────────


def add_group(dsn: str, name: str, *members: str) -> None:
    with psycopg.connect(dsn) as conn:
        for member in members:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                (member, hash_password("test-password")),
            )
        group_id = conn.execute(
            "INSERT INTO groups (name) VALUES (%s) RETURNING id", (name,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO group_members (group_id, user_id) "
            "SELECT %s, id FROM users WHERE username = ANY(%s)",
            (group_id, list(members)),
        )


def folders(dsn: str) -> dict[str, dict]:
    """폴더 경로("RFP/2026") → 범위·부여 그룹·만든 사람. 범위는 최상위만 갖는다."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id, name AS path, visibility, created_by FROM folders WHERE parent_id IS NULL
                UNION ALL
                SELECT f.id, t.path || '/' || f.name, f.visibility, f.created_by
                FROM folders f JOIN tree t ON f.parent_id = t.id)
            SELECT t.path, t.visibility, t.created_by,
                   COALESCE((SELECT array_agg(g.name ORDER BY g.name) FROM folder_grants fg
                             JOIN groups g ON g.id = fg.group_id WHERE fg.folder_id = t.id),
                            ARRAY[]::text[])
            FROM tree t
            """
        ).fetchall()
    return {
        path: {"visibility": visibility, "created_by": creator, "groups": groups}
        for path, visibility, creator, groups in rows
    }


def placement(dsn: str) -> dict[str, dict]:
    """문서 제목 → 든 폴더 경로·폴더 범위 따름 여부·자기 범위·문서 부여 그룹."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id, name AS path FROM folders WHERE parent_id IS NULL
                UNION ALL
                SELECT f.id, t.path || '/' || f.name FROM folders f JOIN tree t ON f.parent_id = t.id)
            SELECT d.title, t.path, d.follows_folder, d.visibility,
                   COALESCE((SELECT array_agg(g.name ORDER BY g.name) FROM document_grants dg
                             JOIN groups g ON g.id = dg.group_id WHERE dg.document_id = d.id),
                            ARRAY[]::text[])
            FROM documents d LEFT JOIN tree t ON t.id = d.folder_id
            """
        ).fetchall()
    return {
        title: {"folder": path, "follows": follows, "visibility": visibility, "groups": groups}
        for title, path, follows, visibility, groups in rows
    }


def rfp_tree(root: Path) -> Path:
    folder = root / "RFP"
    write(folder, "공고.md", "OpenSQL 제안 공고")
    write(folder, "2026/요구사항.txt", "OpenSQL 제안 요구사항")
    write(folder, "2026/평가/배점.md", "OpenSQL 제안 배점")
    return folder


def test_import_keep_folders_rebuilds_the_tree_and_files_each_document_where_it_was(
    archive_db: str, tmp_path: Path
):
    """명세서 「import 폴더 구조」 — 하위 폴더 구조가 같은 이름의 폴더 트리가 되고 문서가 제자리에 든다."""
    source = rfp_tree(tmp_path)

    exit_code = main(
        ["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]
    )

    assert exit_code == 0
    tree = folders(archive_db)
    assert set(tree) == {"RFP", "RFP/2026", "RFP/2026/평가"}
    # 새 최상위 폴더의 기본값은 화면과 같은 조직 공개, 하위 폴더는 범위를 갖지 않는다.
    assert tree["RFP"]["visibility"] == "public"
    assert tree["RFP/2026"]["visibility"] is None
    assert {path: f["created_by"] for path, f in tree.items()} == dict.fromkeys(tree, "bob")
    docs = placement(archive_db)
    assert {title: d["folder"] for title, d in docs.items()} == {
        "공고": "RFP",
        "요구사항": "RFP/2026",
        "배점": "RFP/2026/평가",
    }
    assert all(d["follows"] for d in docs.values())


def test_import_without_keep_folders_still_makes_no_folders(archive_db: str, tmp_path: Path):
    source = rfp_tree(tmp_path)

    assert main(["import", str(source), "--user", "bob", "--dsn", archive_db]) == 0

    assert folders(archive_db) == {}
    assert {d["folder"] for d in placement(archive_db).values()} == {None}


def test_import_grant_group_restricts_the_top_folder_and_hides_it_from_outsiders(
    archive_db: str, tmp_path: Path, capsys, monkeypatch
):
    """명세서 「import 열람 범위」 — 최상위 「RFP」가 「제한 · 사업팀」이 되고 안의 것은 따르며,
    사업팀이 아닌 사용자의 `search`에는 나오지 않는다."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    add_group(archive_db, "사업팀", "alice")
    add_group(archive_db, "개발팀", "dev")
    source = rfp_tree(tmp_path)

    exit_code = main(
        [
            "import", str(source), "--user", "bob", "--keep-folders",
            "--grant-group", "사업팀", "--dsn", archive_db,
        ]
    )

    assert exit_code == 0
    tree = folders(archive_db)
    assert tree["RFP"]["visibility"] == "private"
    assert tree["RFP"]["groups"] == ["사업팀"]
    assert tree["RFP/2026"]["groups"] == []
    assert all(d["follows"] and d["groups"] == [] for d in placement(archive_db).values())

    run_embedding_worker(archive_db)
    capsys.readouterr()
    assert main(["search", "OpenSQL 제안", "--user", "dev", "--dsn", archive_db]) == 0
    outsider = capsys.readouterr().out
    assert "결과가 없습니다" in outsider
    assert main(["search", "OpenSQL 제안", "--user", "alice", "--dsn", archive_db]) == 0
    member = capsys.readouterr().out
    assert "공고" in member and "요구사항" in member and "배점" in member


def test_import_visibility_option_sets_the_top_folder_scope_with_keep_folders(
    archive_db: str, tmp_path: Path
):
    source = rfp_tree(tmp_path)

    exit_code = main(
        [
            "import", str(source), "--user", "bob", "--keep-folders",
            "--visibility", "private", "--dsn", archive_db,
        ]
    )

    assert exit_code == 0
    assert folders(archive_db)["RFP"] == {"visibility": "private", "created_by": "bob", "groups": []}


def test_import_refuses_grant_group_with_an_explicit_public_scope(
    archive_db: str, tmp_path: Path, capsys
):
    add_group(archive_db, "사업팀", "alice")
    source = rfp_tree(tmp_path)

    exit_code = main(
        [
            "import", str(source), "--user", "bob", "--keep-folders", "--visibility", "public",
            "--grant-group", "사업팀", "--dsn", archive_db,
        ]
    )

    assert exit_code == 2
    assert "조직 공개" in capsys.readouterr().out
    assert placement(archive_db) == {}
    assert folders(archive_db) == {}


def test_import_refuses_an_unknown_group_before_making_anything(
    archive_db: str, tmp_path: Path, capsys
):
    source = rfp_tree(tmp_path)

    exit_code = main(
        [
            "import", str(source), "--user", "bob", "--keep-folders",
            "--grant-group", "없는팀", "--dsn", archive_db,
        ]
    )

    assert exit_code == 1
    assert "없는팀" in capsys.readouterr().out
    assert placement(archive_db) == {}
    assert folders(archive_db) == {}


# 결정 ① 재import의 폴더 재사용 기준 — 같은 사용자가 만든 같은 이름의 최상위 폴더를 다시 쓴다.


def test_import_keep_folders_again_reuses_the_same_folders(
    archive_db: str, tmp_path: Path, capsys
):
    source = rfp_tree(tmp_path)
    args = ["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]
    assert main(args) == 0
    write(source, "2026/추가.md", "OpenSQL 제안 추가")
    capsys.readouterr()

    assert main(args) == 0

    assert set(folders(archive_db)) == {"RFP", "RFP/2026", "RFP/2026/평가"}
    assert placement(archive_db)["추가"]["folder"] == "RFP/2026"
    out = capsys.readouterr().out
    assert "가져옴 1건 · 이미 있음 3건" in out
    assert "기존 폴더" in out


def test_import_keep_folders_does_not_reuse_someone_elses_folder_of_the_same_name(
    archive_db: str, tmp_path: Path
):
    source = rfp_tree(tmp_path)
    assert main(["import", str(source), "--user", "alice", "--keep-folders", "--dsn", archive_db]) == 0

    assert main(["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 0

    with psycopg.connect(archive_db) as conn:
        roots = conn.execute(
            "SELECT created_by FROM folders WHERE parent_id IS NULL AND name = 'RFP' ORDER BY 1"
        ).fetchall()
    assert roots == [("alice",), ("bob",)]


def test_import_again_with_a_different_scope_refuses_instead_of_changing_the_folder(
    archive_db: str, tmp_path: Path, capsys
):
    """폴더 범위 변경은 만든 사람의 세션 전용이다(ADR-054 결정 4) — CLI가 기존 폴더 범위를 바꾸지도,
    다른 범위를 기대한 문서를 그 폴더에 조용히 넣지도 않는다."""
    add_group(archive_db, "사업팀", "alice")
    source = rfp_tree(tmp_path)
    assert main(["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 0
    write(source, "추가.md", "OpenSQL 제안 추가")
    capsys.readouterr()

    exit_code = main(
        [
            "import", str(source), "--user", "bob", "--keep-folders",
            "--grant-group", "사업팀", "--dsn", archive_db,
        ]
    )

    assert exit_code == 2
    assert "열람 범위" in capsys.readouterr().out
    assert folders(archive_db)["RFP"]["visibility"] == "public"
    assert "추가" not in placement(archive_db)


def test_import_again_without_scope_options_keeps_the_existing_folder_scope(
    archive_db: str, tmp_path: Path
):
    add_group(archive_db, "사업팀", "alice")
    source = rfp_tree(tmp_path)
    assert main(
        [
            "import", str(source), "--user", "bob", "--keep-folders",
            "--grant-group", "사업팀", "--dsn", archive_db,
        ]
    ) == 0
    write(source, "추가.md", "OpenSQL 제안 추가")

    assert main(["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 0

    assert folders(archive_db)["RFP"]["groups"] == ["사업팀"]
    assert placement(archive_db)["추가"] == {
        "folder": "RFP", "follows": True, "visibility": "private", "groups": []
    }


# 결정 ② --keep-folders 없이 --grant-group — 문서마다 「제한 · 그룹」으로 만든다.


def test_import_grant_group_without_keep_folders_restricts_each_document(
    archive_db: str, tmp_path: Path
):
    add_group(archive_db, "사업팀", "alice")
    source = rfp_tree(tmp_path)

    exit_code = main(
        ["import", str(source), "--user", "bob", "--grant-group", "사업팀", "--dsn", archive_db]
    )

    assert exit_code == 0
    assert folders(archive_db) == {}
    docs = placement(archive_db)
    assert set(docs) == {"공고", "요구사항", "배점"}
    assert all(
        d == {"folder": None, "follows": True, "visibility": "private", "groups": ["사업팀"]}
        for d in docs.values()
    )


# 결정 ③ 폴더에 넣는 문서는 frontmatter visibility와 상관없이 폴더 범위를 따른다 — 들어간 자리의 권한을
# 따르는 것이 실무 이관 도구의 기본이고(SPMT 권한 보존 꺼짐·Drive 폴더 상속), 폴더를 지정해 만든 문서는 개별
# 범위를 받지 않는다(ADR-054). export가 쓰는 `private`은 소유자 전용인지 폴더·그룹 범위인지 구분하지 못한다.


def test_import_keep_folders_follows_the_folder_and_says_frontmatter_visibility_was_not_used(
    archive_db: str, tmp_path: Path, capsys
):
    from test_audit import rows

    source = tmp_path / "팀"
    write(source, "비밀.md", "---\ntitle: 비밀 메모\nvisibility: private\n---\n비밀 본문\n")
    write(source, "공개.md", "---\ntitle: 공개 메모\nvisibility: public\n---\n공개 본문\n")
    write(source, "보통.md", "---\ntitle: 보통 메모\n---\n보통 본문\n")

    assert main(["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 0

    docs = placement(archive_db)
    assert all(d["folder"] == "팀" and d["follows"] for d in docs.values())
    assert "frontmatter의 visibility를 쓰지 않음 2건" in capsys.readouterr().out
    # 생성 뒤에 범위를 바꾸지 않는다 — 열람 범위 변경은 세션 전용이다.
    assert {row[0] for row in rows(archive_db)} == {"document_created"}


def test_import_checks_existing_folders_inside_a_transaction_so_ha_reads_the_primary(
    archive_db: str, tmp_path: Path, monkeypatch
):
    """#180과 같은 함정 — 트랜잭션 밖 폴더 조회는 HA에서 Replica로 가 방금 만든 폴더를 못 보고 두 벌을 만든다."""
    from openarchive import cli

    statuses = []
    real = cli.find_folder

    async def checked(conn, **kwargs):
        statuses.append(conn.info.transaction_status)
        return await real(conn, **kwargs)

    monkeypatch.setattr(cli, "find_folder", checked)
    source = rfp_tree(tmp_path)
    args = ["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]
    assert main(args) == 0
    write(source, "2026/추가.md", "OpenSQL 제안 추가")

    assert main(args) == 0

    assert statuses and set(statuses) == {psycopg.pq.TransactionStatus.INTRANS}
    assert set(folders(archive_db)) == {"RFP", "RFP/2026", "RFP/2026/평가"}


def test_import_keep_folders_makes_no_folder_without_a_document_in_it(
    archive_db: str, tmp_path: Path
):
    """문서가 들어가지 않은 폴더는 남기지 않는다 — 지원하지 않는 형식뿐이거나 넣다가 실패한 폴더도."""
    source = tmp_path / "RFP"
    write(source, "공고.md", "OpenSQL 제안 공고")
    write(source, "그림/로고.xyz", "지원하지 않는 형식")
    write(source, "빈칸/blank.txt", "   \n")
    empty = tmp_path / "빈곳"
    write(empty, "로고.xyz", "지원하지 않는 형식")

    assert main(["import", str(source), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 1
    assert main(["import", str(empty), "--user", "bob", "--keep-folders", "--dsn", archive_db]) == 0

    assert set(folders(archive_db)) == {"RFP"}


# 폴더 없이 --grant-group이면 범위는 문서를 만들 때 정한다 — frontmatter private는 소유자 전용으로 남는다.


def test_import_grant_group_without_folders_keeps_frontmatter_private_owner_only(
    archive_db: str, tmp_path: Path
):
    add_group(archive_db, "사업팀", "alice")
    write(tmp_path, "비밀.md", "---\ntitle: 비밀 메모\nvisibility: private\n---\n비밀 본문\n")
    write(tmp_path, "공개.md", "---\ntitle: 공개 메모\nvisibility: public\n---\n공개 본문\n")

    assert main(
        ["import", str(tmp_path), "--user", "bob", "--grant-group", "사업팀", "--dsn", archive_db]
    ) == 0

    docs = placement(archive_db)
    assert docs["비밀 메모"]["groups"] == []
    assert docs["비밀 메모"]["visibility"] == "private"
    assert docs["공개 메모"]["groups"] == ["사업팀"]
    assert docs["공개 메모"]["visibility"] == "private"


# ── export ─────────────────────────────────────────────────────────────


def test_export_writes_only_owned_documents_as_markdown_with_frontmatter(
    archive_db: str, tmp_path: Path, capsys
):
    """남의 공개 문서는 내보내지 않는다 — 다시 넣으면 소유자가 조용히 바뀐다."""
    seed(
        archive_db,
        lambda conn: create_text_document(
            conn, title="공개 메모", content="공개 본문\n", owner_id="alice", tags=["운영"]
        ),
        lambda conn: create_document(
            conn, filename="plan.txt", data="계획 본문".encode(), owner_id="alice",
            visibility="private",
        ),
        lambda conn: create_text_document(conn, title="남의 글", content="밥", owner_id="bob"),
    )
    target = tmp_path / "out"

    exit_code = main(["export", str(target), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 0
    exported = {path.name: frontmatter(path) for path in target.iterdir()}
    assert exported == {
        "공개 메모.md": (
            {"title": "공개 메모", "tags": ["운영"], "visibility": "public"},
            "공개 본문\n",
        ),
        "plan.md": ({"title": "plan", "tags": [], "visibility": "private"}, "계획 본문"),
    }
    assert "내보냄 2건" in capsys.readouterr().out


def test_export_keeps_every_document_when_titles_collide_or_contain_separators(
    archive_db: str, tmp_path: Path
):
    seed(
        archive_db,
        lambda conn: create_text_document(conn, title="같은 제목", content="하나", owner_id="alice"),
        lambda conn: create_text_document(conn, title="같은 제목", content="둘", owner_id="alice"),
        lambda conn: create_text_document(conn, title="a/b:c", content="셋", owner_id="alice"),
    )
    target = tmp_path / "out"

    assert main(["export", str(target), "--user", "alice", "--dsn", archive_db]) == 0

    files = sorted(target.iterdir())
    assert all(path.parent == target for path in files)
    assert sorted(frontmatter(path)[1] for path in files) == ["둘", "셋", "하나"]
    assert {frontmatter(path)[0]["title"] for path in files} == {"같은 제목", "a/b:c"}


def test_export_names_colliding_titles_by_creation_order_not_last_edit(
    archive_db: str, tmp_path: Path
):
    """제목이 겹치면 먼저 만든 문서가 번호 없는 이름을 갖는다 — 나중에 고쳐도 바뀌지 않는다."""
    created: list[dict] = []

    async def make(conn, content: str) -> None:
        created.append(
            await create_text_document(conn, title="같은 제목", content=content, owner_id="alice")
        )

    seed(
        archive_db,
        lambda conn: make(conn, "먼저"),
        lambda conn: make(conn, "나중"),
        lambda conn: update_tags(conn, created[0]["id"], user_id="alice", tags=["고침"]),
    )
    target = tmp_path / "out"

    assert main(["export", str(target), "--user", "alice", "--dsn", archive_db]) == 0

    assert frontmatter(target / "같은 제목.md")[1] == "먼저"
    assert frontmatter(target / "같은 제목 (2).md")[1] == "나중"


def test_export_skips_documents_whose_text_is_not_recognized_yet(
    archive_db: str, tmp_path: Path, capsys
):
    """인식 중인 스캔은 문서 텍스트가 비어 있다 — 빈 파일을 쓰면 다시 넣을 때 실패한다."""
    scan = (Path(__file__).parent / "fixtures" / "scan_tax_page1.jpg").read_bytes()
    seed(
        archive_db,
        lambda conn: create_document(conn, filename="scan.jpg", data=scan, owner_id="alice"),
    )
    target = tmp_path / "out"

    exit_code = main(["export", str(target), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 0
    assert list(target.iterdir()) == []
    assert "텍스트가 없어 건너뜀 1건" in capsys.readouterr().out


def test_export_refuses_a_folder_that_already_has_files(archive_db: str, tmp_path: Path, capsys):
    """덮어쓰지 않는다 — 이전 export와 섞이면 다시 넣을 때 무엇이 들어갈지 알 수 없다."""
    write(tmp_path, "keep.md", "사용자 파일")

    exit_code = main(["export", str(tmp_path), "--user", "alice", "--dsn", archive_db])

    assert exit_code == 2
    assert (tmp_path / "keep.md").read_text(encoding="utf-8") == "사용자 파일"
    assert "비어 있지 않습니다" in capsys.readouterr().out


def test_export_then_import_preserves_text_tags_and_visibility(archive_db: str, tmp_path: Path):
    """이슈의 검증 항목 — 내보낸 것을 새 설치에 넣으면 문서·태그·열람 범위가 그대로다.

    새 설치는 문서를 지운 같은 DB로 대신한다. 원본 파일은 export 대상이 아니므로
    파일 문서도 문서 텍스트로 돌아온다.
    """
    seed(
        archive_db,
        lambda conn: create_text_document(
            conn, title="원칙", content="# 원칙\n\n본문\n", owner_id="alice", tags=["a", "b"],
            visibility="private",
        ),
        lambda conn: create_document(
            conn, filename="guide.txt", data="안내 본문".encode(), owner_id="alice", tags=["c"]
        ),
    )
    before = [
        (r["title"], r["content"], r["tags"], r["visibility"]) for r in documents(archive_db)
    ]
    target = tmp_path / "out"
    assert main(["export", str(target), "--user", "alice", "--dsn", archive_db]) == 0
    with psycopg.connect(archive_db) as conn:
        conn.execute("DELETE FROM documents")

    assert main(["import", str(target), "--user", "alice", "--dsn", archive_db]) == 0

    after = [
        (r["title"], r["content"], r["tags"], r["visibility"]) for r in documents(archive_db)
    ]
    assert after == before


def test_export_then_import_into_the_same_install_adds_nothing(
    archive_db: str, tmp_path: Path, capsys
):
    """파일 문서도 텍스트가 같으면 이미 있는 것이다 — 원본이 있다는 이유로 두 벌을 만들지 않는다."""
    seed(
        archive_db,
        lambda conn: create_text_document(conn, title="원칙", content="본문", owner_id="alice"),
        lambda conn: create_document(
            conn, filename="guide.txt", data="안내 본문".encode(), owner_id="alice"
        ),
    )
    target = tmp_path / "out"
    assert main(["export", str(target), "--user", "alice", "--dsn", archive_db]) == 0

    assert main(["import", str(target), "--user", "alice", "--dsn", archive_db]) == 0

    assert len(documents(archive_db)) == 2
    assert "가져옴 0건 · 이미 있음 2건" in capsys.readouterr().out


def test_export_refuses_an_unknown_user(archive_db: str, tmp_path: Path, capsys):
    exit_code = main(["export", str(tmp_path / "out"), "--user", "carol", "--dsn", archive_db])

    assert exit_code == 1
    assert "'carol' 계정이 없습니다" in capsys.readouterr().out


# ── search ─────────────────────────────────────────────────────────────


@pytest.fixture
def searchable_db(archive_db: str, monkeypatch) -> str:
    """임베딩까지 끝난 문서 셋. 질의도 같은 프로바이더로 임베딩해야 하므로 fake로 고정한다 —
    개발자의 `~/.openarchive/.env`가 local을 가리켜도 결과가 바뀌지 않게."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    seed(
        archive_db,
        lambda conn: create_text_document(
            conn, title="설치 안내", content="OpenSQL 설치 절차", owner_id="alice", tags=["운영"]
        ),
        lambda conn: create_text_document(
            conn, title="비밀 설치 메모", content="OpenSQL 설치 비밀", owner_id="alice",
            visibility="private",
        ),
    )
    run_embedding_worker(archive_db)
    return archive_db


def test_search_prints_hits_within_what_the_user_can_see(searchable_db: str, capsys):
    exit_code = main(["search", "OpenSQL 설치", "--user", "bob", "--dsn", searchable_db])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "설치 안내" in out
    assert "OpenSQL 설치 절차" in out
    assert "비밀" not in out


def test_search_owner_sees_private_documents(searchable_db: str, capsys):
    assert main(["search", "OpenSQL 설치", "--user", "alice", "--dsn", searchable_db]) == 0

    assert "비밀 설치 메모" in capsys.readouterr().out


def test_search_passes_filters_to_the_single_query(searchable_db: str, capsys):
    exit_code = main(
        ["search", "OpenSQL 설치", "--user", "alice", "--tag", "운영", "--dsn", searchable_db]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "설치 안내" in out
    assert "비밀" not in out


def test_search_says_so_when_nothing_matches(searchable_db: str, capsys):
    exit_code = main(
        ["search", "OpenSQL", "--user", "bob", "--tag", "없는태그", "--dsn", searchable_db]
    )

    assert exit_code == 0
    assert "결과가 없습니다" in capsys.readouterr().out


def test_search_rejects_k_out_of_range(searchable_db: str, capsys):
    exit_code = main(["search", "x", "--user", "bob", "-k", "0", "--dsn", searchable_db])

    assert exit_code == 2
    assert "k는" in capsys.readouterr().out


def test_search_refuses_an_unknown_user(searchable_db: str, capsys):
    exit_code = main(["search", "x", "--user", "carol", "--dsn", searchable_db])

    assert exit_code == 1
    assert "'carol' 계정이 없습니다" in capsys.readouterr().out


# ── ask ────────────────────────────────────────────────────────────────


@pytest.fixture
def askable_db(searchable_db: str, monkeypatch) -> str:
    monkeypatch.setenv("ANSWER_PROVIDER", "fake")
    get_settings.cache_clear()  # searchable_db가 시드하며 이미 설정을 읽어 캐시했다
    return searchable_db


def test_ask_prints_the_answer_and_the_cited_sources_with_versions(askable_db: str, capsys):
    exit_code = main(["ask", "OpenSQL 설치", "--user", "bob", "--dsn", askable_db])

    assert exit_code == 0
    out = capsys.readouterr().out
    answer, sources = out.split("\n근거\n")
    assert "[1]" in answer  # 답 본문이 인용 번호를 단다
    assert "[1] 설치 안내 · v1 기준" in sources
    assert "OpenSQL 설치 절차" in out
    assert "비밀" not in out  # 열람 밖 문서는 인용에도 나오지 않는다


def test_ask_marks_a_source_whose_document_was_revised_since(askable_db: str, capsys):
    """근거가 v1인데 문서가 v2가 됐으면 「v1 기준 · 현재 v2」 (ADR-043 결정 3)."""
    seed(
        askable_db,
        lambda conn: create_text_document(
            conn, title="개정 규정", content="개정 전 정합성 근거", owner_id="alice"
        ),
    )
    run_embedding_worker(askable_db)
    with psycopg.connect(askable_db) as conn:
        (document_id,) = conn.execute(
            "SELECT id FROM documents WHERE title = '개정 규정'"
        ).fetchone()
    seed(
        askable_db,
        lambda conn: update_extracted_text(
            conn, document_id, user_id="alice", content="개정 후 정합성 근거", client_version=1
        ),
    )
    run_embedding_worker(askable_db)

    assert main(["ask", "정합성 근거", "--user", "bob", "--dsn", askable_db]) == 0

    assert "개정 규정 · v1 기준 · 현재 v2" in capsys.readouterr().out


def test_ask_says_when_answering_is_off_without_touching_the_db(monkeypatch, capsys):
    """기본 꺼짐(ADR-043 결정 2) — 꺼져 있으면 DB에 붙지도 않고 켜는 법을 알려 준다."""
    monkeypatch.setenv("ANSWER_PROVIDER", "off")

    exit_code = main(["ask", "x", "--user", "bob", "--dsn", UNREACHABLE_DSN])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ANSWER_PROVIDER" in out
    assert "연결하지 못했습니다" not in out


def test_ask_says_so_when_there_is_no_evidence(askable_db: str, capsys):
    exit_code = main(
        ["ask", "OpenSQL", "--user", "bob", "--tag", "없는태그", "--dsn", askable_db]
    )

    assert exit_code == 0
    assert "근거로 쓸 문서를 찾지 못했습니다" in capsys.readouterr().out


def test_ask_reports_a_failed_generation(askable_db: str, monkeypatch, capsys):
    from openarchive.answers import FakeAnswerProvider

    def unavailable(self, system, prompt):
        raise AnswerUnavailable("connection refused")

    monkeypatch.setattr(FakeAnswerProvider, "generate", unavailable)

    exit_code = main(["ask", "OpenSQL 설치", "--user", "bob", "--dsn", askable_db])

    assert exit_code == 1
    assert "답변 생성에 실패했습니다" in capsys.readouterr().out


def test_ask_rejects_k_out_of_range(askable_db: str, capsys):
    assert main(["ask", "x", "--user", "bob", "-k", "0", "--dsn", askable_db]) == 2
    assert "k는" in capsys.readouterr().out


def test_ask_refuses_an_unknown_user(askable_db: str, capsys):
    assert main(["ask", "x", "--user", "carol", "--dsn", askable_db]) == 1
    assert "'carol' 계정이 없습니다" in capsys.readouterr().out


def test_import_checks_duplicates_inside_a_transaction_so_ha_reads_the_primary(
    archive_db: str, tmp_path: Path, monkeypatch
):
    """#180: HA의 OpenProxy는 트랜잭션 밖 SELECT를 Replica로 보낸다. 방금 만든 문서가 아직 복제되지
    않았으면 중복 판정이 놓쳐 두 벌이 생긴다 — 로컬에는 Replica가 없으니 판정 시점의 상태로 고정한다."""
    from openarchive import cli

    statuses = []

    def spy(real):
        async def checked(conn, **kwargs):
            statuses.append(conn.info.transaction_status)
            return await real(conn, **kwargs)
        return checked

    monkeypatch.setattr(cli, "find_same_original", spy(cli.find_same_original))
    monkeypatch.setattr(cli, "find_same_text", spy(cli.find_same_text))
    write(tmp_path, "a/guide.md", "같은 파일")
    write(tmp_path, "b/guide.md", "같은 파일")
    write(tmp_path, "note.md", "---\ntitle: 메모\n---\n본문\n")

    assert main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db]) == 0

    assert statuses == [psycopg.pq.TransactionStatus.INTRANS] * 3
    assert sorted(r["title"] for r in documents(archive_db)) == ["guide", "메모"]


def test_import_records_actor_for_each_file(archive_db, tmp_path):
    from test_audit import rows

    write(tmp_path, "one.txt", "first file")
    write(tmp_path, "two.md", "# second file")
    write(tmp_path, "three.md", "---\ntitle: Third\ntags: audit\n---\nthird file")
    assert main(["import", str(tmp_path), "--user", "alice", "--dsn", archive_db]) == 0
    audit = rows(archive_db)
    assert len(audit) == 3
    assert all(row[:3] == ("document_created", "alice", "cli") for row in audit)
