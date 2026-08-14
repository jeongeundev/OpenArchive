import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.seed_demo import SeedDocument, load_seed_documents, seed_documents, split_sections


def test_adr_is_split_into_one_document_per_adr_heading():
    source = (ROOT / "docs/ADR.md").read_text()

    documents = split_sections(source, heading_level=3, heading_prefix="ADR-")

    expected_titles = [
        line.removeprefix("### ")
        for line in source.splitlines()
        if line.startswith("### ADR-")
    ]
    assert [document.title for document in documents] == expected_titles
    assert len(documents) == len(expected_titles)


def test_split_sections_preserves_exact_heading_title_and_nonblank_content():
    source = "# 머리말\n\n### ADR-001: 첫 결정\n\n첫 내용\n\n### ADR-002: 둘째 결정\n\n둘째 내용\n"

    documents = split_sections(source, heading_level=3, heading_prefix="ADR-")

    assert documents[0].title == "ADR-001: 첫 결정"
    assert documents[0].content.startswith("### ADR-001: 첫 결정")
    assert all(document.content.strip(" \t\r\n\f") for document in documents)


async def test_seeding_is_idempotent_and_keeps_private_documents(migrated_db: str):
    documents = [
        SeedDocument("공개 문서", "# 공개 문서\n내용", ["문서", "공개"]),
        SeedDocument("참조되는 비공개 문서", "# 비공개 문서\n내용", ["문서", "권한"], "private"),
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
    assert dict(rows) == {"공개 문서": "public", "참조되는 비공개 문서": "private"}


def test_repository_seed_has_tags_private_documents_and_measurement_scale():
    documents = load_seed_documents(ROOT)

    assert len(documents) >= 50
    assert all(document.tags for document in documents)
    assert sum(document.visibility == "private" for document in documents) >= 3
    assert any(
        document.visibility == "private" and document.title.startswith("ADR-018:")
        for document in documents
    )


def test_repository_seed_turns_adr_references_into_full_title_wikilinks():
    documents = load_seed_documents(ROOT)
    titles = {document.title for document in documents}
    targets = [
        target
        for document in documents
        for target in re.findall(r"\[\[([^\[\]]+)\]\]", document.content)
    ]

    assert any(target.startswith("ADR-018:") and target in titles for target in targets)
    assert any(target not in titles for target in targets), "시연용 깨진 링크가 하나는 있어야 한다"


async def test_repository_fragments_pass_the_migrated_content_check(migrated_db: str):
    documents = load_seed_documents(ROOT)

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        created = await seed_documents(conn, documents)
        count = (
            await (
                await conn.execute("SELECT count(*) FROM documents WHERE owner_id = 'seed'")
            ).fetchone()
        )[0]

    assert created == len(documents)
    assert count == len(documents)


async def test_repository_seed_uses_text_documents_and_derives_wikilinks(migrated_db: str):
    documents = load_seed_documents(ROOT)

    async with await psycopg.AsyncConnection.connect(migrated_db, autocommit=True) as conn:
        await seed_documents(conn, documents)
        metadata = await (
            await conn.execute(
                """
                SELECT count(*) FILTER (WHERE filename IS NOT NULL),
                       count(*) FILTER (WHERE content_type <> 'md')
                FROM documents
                WHERE owner_id = 'seed'
                """
            )
        ).fetchone()
        resolved_adr_links = (
            await (
                await conn.execute(
                    """
                    SELECT count(*)
                    FROM document_links l
                    JOIN documents src ON src.id = l.src_document_id
                    JOIN documents dst ON dst.title = l.target_title
                    WHERE src.owner_id = 'seed'
                      AND dst.owner_id = 'seed'
                      AND l.target_title LIKE 'ADR-018:%'
                    """
                )
            ).fetchone()
        )[0]

    assert metadata == (0, 0)
    assert resolved_adr_links > 0
