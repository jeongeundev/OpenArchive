import re
import sys
from collections import Counter
from pathlib import Path

import psycopg
import pytest

from app.services.chunking import chunk_text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.seed_demo import (
    CORPUS_ROOT,
    SeedDocument,
    load_seed_documents,
    parse_seed_document,
    seed_documents,
)


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


def test_every_corpus_document_fits_in_one_chunk():
    """청크가 2개 이상이면 `overlaps` 관계가 무의미해진다.

    008 트리거는 청크마다 가장 가까운 10개를 이웃으로 잡고, 겹친 청크 비율이 0.8
    이상이면서 2개 이상이면 "전반적으로 같은 내용"(overlaps)으로 판정한다. 코퍼스
    전체가 66청크뿐이라 이웃 10개가 전체의 15%를 덮으므로, 2청크 문서는 두 청크가
    모두 걸리기만 하면 2/2 = 1.0으로 이 하한을 통과한다. 실측에서 「재택근무 운영
    지침」이 「경비 정산 처리 기준」과 겹친다고 저장됐고, 3청크로 늘려도 오탐이 13쌍
    남았다. 모든 문서를 1청크로 두면 matched_chunks >= 2가 성립할 수 없다.
    """
    over = [
        (document.title, len(chunk_text(document.content)))
        for document in load_seed_documents()
        if len(chunk_text(document.content)) != 1
    ]

    assert over == []


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
        first = await seed_documents(conn, documents)
        second = await seed_documents(conn, documents)
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
        created = await seed_documents(conn, documents)
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
