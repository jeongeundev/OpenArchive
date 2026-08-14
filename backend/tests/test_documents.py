import hashlib

import psycopg
import pytest

from app.services.documents import (
    MAX_EXTRACTED_TEXT_LENGTH,
    EmptyExtractedText,
    ExtractedTextTooLarge,
    create_document,
    create_text_document,
)
from app.services.parsing import UnsupportedFileType


@pytest.fixture
async def documents_conn(migrated_db: str):
    async with await psycopg.AsyncConnection.connect(
        migrated_db, autocommit=True
    ) as conn:
        yield conn


async def document_count(conn: psycopg.AsyncConnection) -> int:
    row = await (await conn.execute("SELECT count(*) FROM documents")).fetchone()
    return row[0]


async def test_create_text_document_stores_text_metadata_and_trigger_derivatives(
    documents_conn,
):
    content = "OpenSQL 문서 텍스트"

    document = await create_text_document(
        documents_conn,
        title="직접 공급",
        content=content,
        content_type="txt",
        owner_id="alice",
        visibility="private",
    )

    row = await (
        await documents_conn.execute(
            """
            SELECT title, filename, content, content_type, content_hash, owner_id, visibility
            FROM documents WHERE id = %s
            """,
            (document["id"],),
        )
    ).fetchone()
    assert row == (
        "직접 공급",
        None,
        content,
        "txt",
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "alice",
        "private",
    )
    assert await (
        await documents_conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s",
            (document["id"],),
        )
    ).fetchone() == (1,)
    assert await (
        await documents_conn.execute(
            "SELECT version FROM document_versions WHERE document_id = %s",
            (document["id"],),
        )
    ).fetchone() == (1,)


@pytest.mark.parametrize("content", [" ", "\t\r\n\f"])
async def test_create_text_document_rejects_blank_content_without_saving(
    documents_conn, content
):
    with pytest.raises(EmptyExtractedText, match="문서 텍스트는 비어 있을 수 없습니다"):
        await create_text_document(
            documents_conn, title="빈 입력", content=content, owner_id="alice"
        )

    assert await document_count(documents_conn) == 0


async def test_create_text_document_rejects_oversized_content_without_saving(
    documents_conn,
):
    with pytest.raises(ExtractedTextTooLarge, match="500KB"):
        await create_text_document(
            documents_conn,
            title="초과 입력",
            content="x" * (MAX_EXTRACTED_TEXT_LENGTH + 1),
            owner_id="alice",
        )

    assert await document_count(documents_conn) == 0


async def test_create_text_document_rejects_binary_content_type_without_saving(
    documents_conn,
):
    with pytest.raises(UnsupportedFileType, match="txt, md"):
        await create_text_document(
            documents_conn,
            title="PDF 텍스트",
            content="직접 공급",
            content_type="pdf",
            owner_id="alice",
        )

    assert await document_count(documents_conn) == 0


async def test_create_text_document_normalizes_tags(documents_conn):
    document = await create_text_document(
        documents_conn,
        title="태그",
        content="태그 정규화",
        owner_id="alice",
        tags=[" search ", "db", "search", "", " db "],
    )

    assert document["tags"] == ["search", "db"]


async def test_create_document_keeps_filename_stem_and_trigger_derivatives(
    documents_conn,
):
    document = await create_document(
        documents_conn,
        filename="uploaded-guide.md",
        data="업로드 추출 텍스트".encode(),
        owner_id="alice",
    )

    assert document["title"] == "uploaded-guide"
    assert document["filename"] == "uploaded-guide.md"
    assert await (
        await documents_conn.execute(
            "SELECT count(*) FROM embedding_jobs WHERE document_id = %s",
            (document["id"],),
        )
    ).fetchone() == (1,)
    assert await (
        await documents_conn.execute(
            "SELECT version FROM document_versions WHERE document_id = %s",
            (document["id"],),
        )
    ).fetchone() == (1,)
