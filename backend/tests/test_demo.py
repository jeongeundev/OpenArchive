"""예제 코퍼스(`openarchive/demo_corpus/`)와 `openarchive demo` (#95-d).

코퍼스는 패키지에 실려 pip 설치본에서도 `openarchive demo` 한 줄로 들어간다. 측정용
`scripts/seed_demo.py`도 같은 적재 로직을 쓴다(test_seed_demo.py).
"""

import re
from collections import Counter
from pathlib import Path

import psycopg
import pytest
from conftest import background_worker, run_embedding_worker

import openarchive
from openarchive.cli import main
from openarchive.demo import (
    CORPUS_ROOT,
    SeedDocument,
    load_seed_documents,
    parse_seed_document,
    seed_documents,
    summarize,
)
from openarchive.services.auth import hash_password
from openarchive.services.chunking import chunk_text


def test_front_matter_supplies_tags_and_body_keeps_the_heading():
    text = "---\ntags: 인사, 근무제도\n---\n# 재택근무 운영 지침\n\n본문 한 줄\n"

    document = parse_seed_document(text)

    assert document.title == "재택근무 운영 지침"
    assert document.tags == ["인사", "근무제도"]
    assert document.visibility == "public"
    assert document.content.startswith("# 재택근무 운영 지침")
    assert "본문 한 줄" in document.content
    assert "tags:" not in document.content


def test_title_in_front_matter_wins_over_the_heading():
    """본문이 같은 문서 두 벌을 서로 다른 제목으로 넣기 위한 통로다."""
    text = "---\ntitle: 안전관리 수칙 (현장 게시본)\ntags: 물류, 안전\n---\n# 물류센터 안전관리 수칙\n\n본문\n"

    document = parse_seed_document(text)

    assert document.title == "안전관리 수칙 (현장 게시본)"
    assert document.content.startswith("# 물류센터 안전관리 수칙")


def test_visibility_is_read_from_front_matter():
    text = "---\ntags: 인사, 평가\nvisibility: private\n---\n# 상반기 인사평가\n\n본문\n"

    assert parse_seed_document(text).visibility == "private"


@pytest.mark.parametrize(
    "text",
    [
        "# 머리말 없는 문서\n\n본문\n",
        "---\ntags: 인사\n---\n제목 헤딩이 없는 본문\n",
        "---\ntags: 인사\nvisibility: secret\n---\n# 제목\n\n본문\n",
        "---\ntitle: 제목만 있음\n---\n# 제목\n\n본문\n",
    ],
)
def test_malformed_corpus_files_are_rejected(text: str):
    with pytest.raises(ValueError):
        parse_seed_document(text)


def test_corpus_covers_four_departments_at_measurable_scale():
    documents = load_seed_documents()

    assert len(documents) >= 50
    assert all(document.tags for document in documents)
    departments = Counter(document.tags[0] for document in documents)
    # 인사와 재무는 실측에서 서로 분리되지 않았다 — 문서를 절 단위로 잘라 재봐도
    # 같은-부서 이웃 비율이 0.55에서 0.56으로만 움직였다. 데이터가 한 덩어리라고
    # 말하므로 taxonomy도 경영지원 하나로 둔다.
    assert set(departments) == {"경영지원", "고객지원", "물류", "보안"}
    # 트리거는 청크마다 가장 가까운 10개를 이웃으로 잡는다(008). 부서가 그보다 작으면
    # 이웃이 반드시 부서 밖으로 넘쳐 덩어리가 갈리지 않는다.
    assert min(departments.values()) >= 12


def test_corpus_titles_are_unique():
    """seed_documents가 제목으로 중복을 거르므로 제목이 겹치면 문서가 조용히 사라진다."""
    titles = [document.title for document in load_seed_documents()]

    assert len(titles) == len(set(titles))


def test_corpus_mixes_single_and_multi_chunk_documents():
    """014의 양쪽 비율·문서당 상한이 2청크 편향을 막았으므로 길이를 섞어
    새 규칙이 여러 청크 문서에서 성립함을 시연한다.
    """
    documents = load_seed_documents()
    counts = [len(chunk_text(document.content)) for document in documents]
    multi_by_department = Counter(
        document.tags[0]
        for document, count in zip(documents, counts)
        if count >= 2
    )

    assert set(multi_by_department) == {"경영지원", "고객지원", "물류", "보안"}
    assert min(multi_by_department.values()) >= 3
    assert max(counts) <= 4
    assert counts.count(1) >= 30


def test_corpus_has_private_documents_in_more_than_one_domain():
    documents = load_seed_documents()

    private = [document for document in documents if document.visibility == "private"]
    assert len(private) >= 3
    assert len({document.tags[0] for document in private}) >= 2


def test_corpus_wikilinks_resolve_except_one_broken_target():
    documents = load_seed_documents()
    titles = {document.title for document in documents}
    targets = {
        target
        for document in documents
        for target in re.findall(r"\[\[([^\[\]]+)\]\]", document.content)
    }

    assert len(targets & titles) >= 30, "위키링크 대부분은 실제 문서를 가리켜야 한다"
    assert targets - titles == {"재해복구 훈련 계획"}


def test_corpus_links_cross_domains():
    """도메인이 링크로 이어져야 검색의 관계 확장이 덩어리를 넘어간다."""
    documents = load_seed_documents()
    domain_by_title = {document.title: document.tags[0] for document in documents}

    crossing = {
        (document.tags[0], domain_by_title[target])
        for document in documents
        for target in re.findall(r"\[\[([^\[\]]+)\]\]", document.content)
        if target in domain_by_title and domain_by_title[target] != document.tags[0]
    }

    assert len(crossing) >= 8


def test_corpus_contains_one_identical_text_pair():
    documents = load_seed_documents()

    by_content: dict[str, list[str]] = {}
    for document in documents:
        by_content.setdefault(document.content, []).append(document.title)
    duplicates = [titles for titles in by_content.values() if len(titles) > 1]

    assert len(duplicates) == 1
    assert len(duplicates[0]) == 2


def test_corpus_files_live_under_one_directory_per_domain():
    assert CORPUS_ROOT.is_dir()
    domain_dirs = sorted(path.name for path in CORPUS_ROOT.iterdir() if path.is_dir())
    assert domain_dirs == ["cs", "finance", "hr", "logistics", "security"]


async def test_seeding_is_idempotent_and_keeps_private_documents(migrated_db: str):
    documents = [
        SeedDocument("공개 문서", "# 공개 문서\n내용", ["문서", "공개"]),
        SeedDocument("비공개 문서", "# 비공개 문서\n내용", ["문서", "권한"], "private"),
    ]

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        first = await seed_documents(conn, documents, "seed")
        second = await seed_documents(conn, documents, "seed")
        rows = await (
            await conn.execute(
                "SELECT title, visibility FROM documents WHERE owner_id = 'seed' ORDER BY title"
            )
        ).fetchall()

    assert first == 2
    assert second == 0
    assert dict(rows) == {"공개 문서": "public", "비공개 문서": "private"}


async def test_seeding_can_target_the_demo_login_account(migrated_db: str):
    """소유자를 고를 수 있어야 비공개 문서를 시연에서 보여줄 수 있다.

    비공개 문서는 소유자에게만 보인다(ADR-018). 코퍼스가 `seed` 소유인데 시연은
    `admin`으로 로그인하면 비공개 4건이 처음부터 보이지 않아, 「다른 계정에게는
    존재하지 않는 것처럼 보인다」를 보여줄 수 없다.
    """
    documents = [
        SeedDocument("공개", "# 공개\n내용", ["문서"]),
        SeedDocument("비공개", "# 비공개\n내용", ["문서"], "private"),
    ]

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        created = await seed_documents(conn, documents, owner="admin")
        rows = await (
            await conn.execute("SELECT title, owner_id FROM documents ORDER BY title")
        ).fetchall()
        # 다른 소유자로 다시 넣으면 별개 문서다 — 중복 판정은 소유자 안에서만 한다.
        again = await seed_documents(conn, documents, owner="seed")
        owners = await (
            await conn.execute("SELECT count(DISTINCT owner_id) FROM documents")
        ).fetchone()

    assert created == 2
    assert dict(rows) == {"공개": "admin", "비공개": "admin"}
    assert again == 2
    assert owners == (2,)


async def test_corpus_loads_as_text_documents_with_resolved_wikilinks(migrated_db: str):
    documents = load_seed_documents()

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        created = await seed_documents(conn, documents, "seed")
        metadata = await (
            await conn.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE filename IS NOT NULL),
                       count(*) FILTER (WHERE content_type <> 'md')
                FROM documents
                WHERE owner_id = 'seed'
                """
            )
        ).fetchone()
        resolved, unresolved = await (
            await conn.execute(
                """
                SELECT count(*) FILTER (WHERE d.id IS NOT NULL),
                       count(*) FILTER (WHERE d.id IS NULL)
                FROM document_links l
                JOIN documents src ON src.id = l.src_document_id AND src.owner_id = 'seed'
                LEFT JOIN documents d ON d.title = l.target_title
                """
            )
        ).fetchone()

    assert created == len(documents)
    assert metadata == (len(documents), 0, 0)
    assert resolved >= 30
    assert unresolved == 1




async def test_summary_counts_only_edges_touching_the_owners_documents(migrated_db: str):
    """이미 문서가 있는 DB에 넣어도 요약은 그 계정의 예제 결과만 센다.

    다른 계정 문서끼리의 관계를 세면 "관계 N쌍"이 예제가 만든 수치가 아니게 된다.
    예제 문서와 기존 문서 사이의 관계는 예제가 만든 것이므로 센다.
    """
    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await seed_documents(
            conn,
            [SeedDocument("기존 1", "# 기존 1\n내용", ["문서"]), SeedDocument("기존 2", "# 기존 2\n내용", ["문서"])],
            "bob",
        )
        await seed_documents(conn, [SeedDocument("예제", "# 예제\n내용", ["문서"])], "alice")
        ids = dict(await (await conn.execute("SELECT title, id FROM documents")).fetchall())
        # 판정 결과를 흉내 내려고 관계를 직접 넣는다 — 세는 범위만 본다.
        await conn.execute(
            """
            INSERT INTO document_edges (src_document_id, dst_document_id, kind, score)
            VALUES (%(a)s, %(b)s, 'related', 0.9), (%(b)s, %(a)s, 'related', 0.9),
                   (%(c)s, %(a)s, 'related', 0.9)
            """,
            {"a": ids["기존 1"], "b": ids["기존 2"], "c": ids["예제"]},
        )

        _, edge_pairs = await summarize(conn, "alice")

    assert edge_pairs == 1



def test_corpus_ships_inside_the_package():
    """pip 설치본에는 저장소의 scripts/가 없다 — 코퍼스가 openarchive 패키지 안에 있어야 demo가 돈다."""
    assert CORPUS_ROOT.parent == Path(openarchive.__file__).resolve().parent
    assert len(load_seed_documents()) == 64


# ── openarchive demo ─────────────────────────────────────────────────────────


@pytest.fixture
def demo_db(migrated_db: str) -> str:
    """alice·bob 두 계정이 있는 DB. demo는 --user 계정이 실제로 있는지부터 확인한다."""
    with psycopg.connect(migrated_db) as conn:
        for username in ("alice", "bob"):
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, hash_password("test-password")),
            )
    return migrated_db


def owned(dsn: str) -> dict[str, tuple[int, int]]:
    """소유자별 (문서 수, 비공개 수)."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT owner_id, count(*), count(*) FILTER (WHERE visibility = 'private')
            FROM documents GROUP BY owner_id
            """
        ).fetchall()
    return {owner: (total, private) for owner, total, private in rows}


def test_demo_loads_the_corpus_as_the_given_user(demo_db: str, capsys):
    """비공개 문서는 소유자에게만 보인다 — 로그인할 계정 소유여야 4건이 화면에 나온다(ADR-018)."""
    exit_code = main(["demo", "--user", "alice", "--no-wait", "--dsn", demo_db])

    assert exit_code == 0
    assert owned(demo_db) == {"alice": (64, 4)}
    output = capsys.readouterr().out
    assert "예제 64건" in output
    assert "openarchive serve" in output
    assert "openarchive rebuild-edges" in output


def test_demo_again_adds_nothing(demo_db: str, capsys):
    assert main(["demo", "--user", "alice", "--no-wait", "--dsn", demo_db]) == 0
    capsys.readouterr()

    assert main(["demo", "--user", "alice", "--no-wait", "--dsn", demo_db]) == 0

    assert owned(demo_db) == {"alice": (64, 4)}
    assert "새로 넣은 문서 0건" in capsys.readouterr().out


def test_demo_refuses_an_unknown_user_before_touching_anything(demo_db: str, capsys):
    exit_code = main(["demo", "--user", "carol", "--no-wait", "--dsn", demo_db])

    assert exit_code == 1
    assert owned(demo_db) == {}
    assert "'carol' 계정이 없습니다" in capsys.readouterr().out


def test_demo_waits_for_embedding_then_rebuilds_edges(demo_db: str, capsys):
    """기본 동작은 워커가 다 처리할 때까지 기다린 뒤 관계를 전체 기준으로 맞추는 것이다(ADR-029 결정 6)."""
    assert main(["demo", "--user", "alice", "--no-wait", "--dsn", demo_db]) == 0
    assert run_embedding_worker(demo_db) == 64 * 2
    with psycopg.connect(demo_db) as conn:
        (first_id,) = conn.execute(
            "SELECT id FROM documents ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        # 재계산이 판정을 실제로 다시 돌리는지 보려고 한 문서의 관계를 비운다.
        conn.execute("DELETE FROM document_edges WHERE src_document_id = %s", (first_id,))
    capsys.readouterr()

    with background_worker(demo_db):  # 관계 재계산은 워커가 관계 잡으로 한다 (#156)
        exit_code = main(["demo", "--user", "alice", "--timeout", "10", "--dsn", demo_db])

    assert exit_code == 0
    with psycopg.connect(demo_db) as conn:
        (restored,) = conn.execute(
            "SELECT count(*) FROM document_edges WHERE src_document_id = %s", (first_id,)
        ).fetchone()
    assert restored > 0
    output = capsys.readouterr().out
    assert "관계 재계산 64건" in output
    # 진행 표시는 \r로 한 줄을 덮어쓴다 — 결과 줄은 그 뒤에 새 줄로 시작해야 한다.
    assert "64/64\n완료:" in output


def test_demo_gives_up_waiting_without_a_worker(demo_db: str, capsys):
    """워커가 없으면 임베딩이 오지 않는다 — 기다리다 트레이스백 대신 serve를 안내한다."""
    exit_code = main(["demo", "--user", "alice", "--timeout", "0", "--dsn", demo_db])

    assert exit_code == 1
    assert owned(demo_db) == {"alice": (64, 4)}
    assert "0초 안에 임베딩이 끝나지 않았습니다" in capsys.readouterr().out


def test_demo_gives_up_waiting_for_edge_jobs_without_a_worker(demo_db: str, capsys):
    """임베딩은 끝났는데 워커가 사라졌다 — 관계 잡은 걸려 있으니 rebuild-edges를 다시 할 필요가 없다."""
    assert main(["demo", "--user", "alice", "--no-wait", "--dsn", demo_db]) == 0
    run_embedding_worker(demo_db)
    capsys.readouterr()

    exit_code = main(["demo", "--user", "alice", "--timeout", "1", "--dsn", demo_db])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "관계 잡" in output
    assert "openarchive serve" in output
    assert "rebuild-edges" not in output
