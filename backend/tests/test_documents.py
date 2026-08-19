import hashlib

import psycopg
import pytest

from app.services.documents import (
    MAX_EXTRACTED_TEXT_LENGTH,
    DocumentAccessDenied,
    DocumentNotFound,
    EmptyExtractedText,
    ExtractedTextTooLarge,
    InvalidVisibility,
    VersionConflict,
    create_document,
    create_text_document,
    get_document_version,
    restore_version,
    update_extracted_text,
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


@pytest.mark.parametrize("visibility", ["internal", "public "])
async def test_create_text_document_rejects_invalid_visibility_without_saving(
    documents_conn, visibility
):
    with pytest.raises(InvalidVisibility, match="public, private"):
        await create_text_document(
            documents_conn,
            title="잘못된 공개범위",
            content="문서 텍스트",
            owner_id="alice",
            visibility=visibility,
        )

    assert await document_count(documents_conn) == 0


async def test_create_document_rejects_invalid_visibility_without_saving(documents_conn):
    with pytest.raises(InvalidVisibility, match="public, private"):
        await create_document(
            documents_conn,
            filename="invalid.md",
            data="추출 텍스트".encode(),
            owner_id="alice",
            visibility="internal",
        )

    assert await document_count(documents_conn) == 0


@pytest.mark.parametrize("visibility", ["public", "private"])
async def test_create_text_document_accepts_visibility_values(
    documents_conn, visibility
):
    document = await create_text_document(
        documents_conn,
        title="정상 공개범위",
        content="문서 텍스트",
        owner_id="alice",
        visibility=visibility,
    )

    assert document["visibility"] == visibility


async def test_create_text_document_normalizes_tags(documents_conn):
    document = await create_text_document(
        documents_conn,
        title="태그",
        content="태그 정규화",
        owner_id="alice",
        tags=[" search ", "db", "search", "", " db "],
    )

    assert document["tags"] == ["search", "db"]


async def test_edit_rejection_calls_text_by_its_name_for_each_origin(documents_conn):
    """거절 문구가 원본 파일 유무를 따른다 (ADR-035 결정 3).

    편집 경로는 두 진입점이 만든 문서를 모두 받는다. 직접 공급 문서에 "추출 텍스트"라
    답하면, 추출한 대상이 없는데 추출을 말하는 문구가 사용자 화면에 그대로 나온다 —
    `TextEditor`가 서버의 `detail`을 출력하기 때문이다.
    """
    supplied = await create_text_document(
        documents_conn, title="직접 공급", content="문서 텍스트", owner_id="alice"
    )
    uploaded = await create_document(
        documents_conn,
        filename="uploaded.md",
        data="추출된 텍스트".encode(),
        owner_id="alice",
    )

    for document, expected in ((supplied, "문서 텍스트"), (uploaded, "추출 텍스트")):
        with pytest.raises(EmptyExtractedText, match=f"{expected}는 비어 있을 수 없습니다"):
            await update_extracted_text(
                documents_conn,
                document["id"],
                user_id="alice",
                content="   ",
                client_version=document["version"],
            )
        with pytest.raises(ExtractedTextTooLarge, match=f"{expected}는 500KB"):
            await update_extracted_text(
                documents_conn,
                document["id"],
                user_id="alice",
                content="x" * (MAX_EXTRACTED_TEXT_LENGTH + 1),
                client_version=document["version"],
            )


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


async def test_get_document_version_returns_that_versions_own_text(documents_conn):
    """과거 버전 조회는 그 시점의 본문을 준다 (ADR-037 결정 1).

    현재 본문을 돌려주면 이력이 있다는 사실만 보여줄 뿐 아무것도 답하지 못한다.
    """
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )
    await update_extracted_text(
        documents_conn,
        document["id"],
        user_id="alice",
        content="고친 내용",
        client_version=document["version"],
    )

    first = await get_document_version(
        documents_conn, document["id"], version=1, user_id="alice"
    )
    second = await get_document_version(
        documents_conn, document["id"], version=2, user_id="alice"
    )

    assert first["content"] == "처음 내용"
    assert first["version"] == 1
    assert second["content"] == "고친 내용"


async def test_get_document_version_hides_private_versions_from_others(documents_conn):
    """볼 수 없는 문서의 버전은 존재하지 않는 것처럼 막힌다 (ADR-027).

    버전 번호의 존재 여부만 알려줘도 문서가 있다는 사실과 수정 횟수가 새어 나간다.
    """
    private = await create_text_document(
        documents_conn,
        title="비공개",
        content="남에게 보이면 안 되는 내용",
        owner_id="alice",
        visibility="private",
    )
    public = await create_text_document(
        documents_conn, title="공개", content="누구나 보는 내용", owner_id="alice"
    )

    with pytest.raises(DocumentNotFound):
        await get_document_version(
            documents_conn, private["id"], version=1, user_id="bob"
        )
    visible = await get_document_version(
        documents_conn, public["id"], version=1, user_id="bob"
    )
    assert visible["content"] == "누구나 보는 내용"


async def test_get_document_version_rejects_version_that_never_existed(documents_conn):
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )

    with pytest.raises(DocumentNotFound):
        await get_document_version(
            documents_conn, document["id"], version=2, user_id="alice"
        )


async def test_restore_version_appends_a_new_version_instead_of_rewinding(
    documents_conn,
):
    """복원은 되감기가 아니라 새 버전 생성이다 (ADR-037 결정 2).

    이력이 append-only여야 정합성 검증 쿼리(`c.version <> d.version`)의 기준이
    흔들리지 않는다. v1으로 되돌리면 v3이 생기고 v1·v2는 그대로 남아야 한다.
    """
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )
    await update_extracted_text(
        documents_conn,
        document["id"],
        user_id="alice",
        content="고친 내용",
        client_version=1,
    )

    restored = await restore_version(
        documents_conn, document["id"], version=1, user_id="alice", client_version=2
    )

    assert restored["version"] == 3
    assert restored["content"] == "처음 내용"
    assert await (
        await documents_conn.execute(
            "SELECT version, content FROM document_versions"
            " WHERE document_id = %s ORDER BY version",
            (document["id"],),
        )
    ).fetchall() == [(1, "처음 내용"), (2, "고친 내용"), (3, "처음 내용")]


async def test_restore_version_reruns_the_derivation_pipeline(documents_conn):
    """복원도 편집과 같은 파생 계약을 탄다 — 잡이 생기고 상태가 pending으로 돌아간다."""
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )
    await update_extracted_text(
        documents_conn,
        document["id"],
        user_id="alice",
        content="고친 내용",
        client_version=1,
    )
    await documents_conn.execute(
        "UPDATE documents SET embedding_status = 'ready' WHERE id = %s",
        (document["id"],),
    )
    await documents_conn.execute(
        "UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s",
        (document["id"],),
    )

    await restore_version(
        documents_conn, document["id"], version=1, user_id="alice", client_version=2
    )

    assert await (
        await documents_conn.execute(
            "SELECT embedding_status FROM documents WHERE id = %s", (document["id"],)
        )
    ).fetchone() == ("pending",)
    assert await (
        await documents_conn.execute(
            "SELECT count(*) FROM embedding_jobs"
            " WHERE document_id = %s AND status = 'pending'",
            (document["id"],),
        )
    ).fetchone() == (1,)


async def test_restore_version_rejects_a_stale_client_version(documents_conn):
    """복원도 편집과 같은 낙관적 잠금을 쓴다 (ADR-037 결정 3).

    복원은 파괴적으로 보이지 않지만 현재 내용을 밀어내므로 편집과 같은 무게다.
    """
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )
    await update_extracted_text(
        documents_conn,
        document["id"],
        user_id="alice",
        content="고친 내용",
        client_version=1,
    )

    with pytest.raises(VersionConflict):
        await restore_version(
            documents_conn, document["id"], version=1, user_id="alice", client_version=1
        )
    assert await (
        await documents_conn.execute(
            "SELECT content FROM documents WHERE id = %s", (document["id"],)
        )
    ).fetchone() == ("고친 내용",)


async def test_restore_version_refuses_a_non_owner_of_a_visible_document(
    documents_conn,
):
    """공개 문서라도 복원은 소유자만 한다 — 편집과 같은 쓰기 권한 규칙이다."""
    document = await create_text_document(
        documents_conn, title="공개 정책", content="처음 내용", owner_id="alice"
    )
    await update_extracted_text(
        documents_conn,
        document["id"],
        user_id="alice",
        content="고친 내용",
        client_version=1,
    )

    with pytest.raises(DocumentAccessDenied):
        await restore_version(
            documents_conn, document["id"], version=1, user_id="bob", client_version=2
        )


async def test_restore_version_rejects_a_version_that_never_existed(documents_conn):
    document = await create_text_document(
        documents_conn, title="정책", content="처음 내용", owner_id="alice"
    )

    with pytest.raises(DocumentNotFound):
        await restore_version(
            documents_conn, document["id"], version=7, user_id="alice", client_version=1
        )
