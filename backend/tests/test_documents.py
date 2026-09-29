import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.services.documents import (
    MAX_EXTRACTED_TEXT_LENGTH,
    DocumentAccessDenied,
    DocumentNotFound,
    EmptyExtractedText,
    ExtractedTextTooLarge,
    IdempotencyKeyReused,
    InvalidVisibility,
    VersionConflict,
    apply_extracted_text,
    create_document,
    create_text_document,
    get_document_version,
    replace_original_file,
    restore_version,
    update_extracted_text,
)
from app.services.parsing import UnsupportedFileType
from test_parsing import minimal_pdf


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


async def count_rows(conn: psycopg.AsyncConnection, table: str) -> int:
    row = await (await conn.execute(f"SELECT count(*) FROM {table}")).fetchone()
    return row[0]


async def test_create_text_document_with_the_same_key_returns_the_first_document(
    documents_conn,
):
    """모호한 커밋 뒤의 재시도는 문서를 두 번 만들지 않는다 (ADR-047)."""
    request = {"title": "재시도", "content": "본문", "owner_id": "alice", "idempotency_key": "k-1"}

    first = await create_text_document(documents_conn, **request)
    second = await create_text_document(documents_conn, **request)

    assert second["id"] == first["id"]
    assert await document_count(documents_conn) == 1


async def test_create_document_with_the_same_key_stores_one_document_and_one_original(
    documents_conn,
):
    request = {
        "filename": "guide.md",
        "data": "업로드 본문".encode(),
        "owner_id": "alice",
        "idempotency_key": "k-1",
    }

    first = await create_document(documents_conn, **request)
    second = await create_document(documents_conn, **request)

    assert second["id"] == first["id"]
    assert await document_count(documents_conn) == 1
    assert await count_rows(documents_conn, "document_files") == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"content": "다른 본문"},
        {"title": "다른 제목"},
        {"tags": ["다른"]},
        {"visibility": "private"},
    ],
)
async def test_the_same_key_with_a_different_request_is_rejected(documents_conn, changed):
    """같은 키에 다른 본문은 재시도가 아니라 키 재사용이다. 처음 문서를 돌려주면
    호출자는 자기가 보낸 것이 저장됐다고 믿는다."""
    request = {"title": "재시도", "content": "본문", "owner_id": "alice", "idempotency_key": "k-1"}
    await create_text_document(documents_conn, **request)

    with pytest.raises(IdempotencyKeyReused):
        await create_text_document(documents_conn, **{**request, **changed})
    assert await document_count(documents_conn) == 1


async def test_a_key_used_for_a_text_document_cannot_create_an_upload(documents_conn):
    await create_text_document(
        documents_conn, title="guide", content="본문", owner_id="alice", idempotency_key="k-1"
    )

    with pytest.raises(IdempotencyKeyReused):
        await create_document(
            documents_conn,
            filename="guide.md",
            data="본문".encode(),
            owner_id="alice",
            idempotency_key="k-1",
        )
    assert await document_count(documents_conn) == 1


async def test_the_same_key_from_another_owner_creates_its_own_document(documents_conn):
    """키는 소유자 범위다 — 남의 키와 부딪히지 않고, 남의 문서를 돌려받지도 않는다."""
    alice = await create_text_document(
        documents_conn, title="t", content="본문", owner_id="alice", idempotency_key="k-1"
    )
    bob = await create_text_document(
        documents_conn, title="t", content="본문", owner_id="bob", idempotency_key="k-1"
    )

    assert bob["id"] != alice["id"]
    assert bob["owner_id"] == "bob"
    assert await document_count(documents_conn) == 2


async def test_a_concurrent_request_with_the_same_key_waits_and_returns_the_first_document(
    migrated_db, documents_conn
):
    """동시 요청은 기본키가 직렬화한다 — 뒤 요청은 앞 트랜잭션이 끝나기를 기다렸다가
    커밋된 문서를 돌려준다. 앞 트랜잭션을 열어 둔 채로 뒤 요청을 보내 겹침을 보장한다."""
    request = {"title": "동시", "content": "본문", "owner_id": "alice", "idempotency_key": "k-1"}
    async with await psycopg.AsyncConnection.connect(migrated_db) as first_conn:
        first = await create_text_document(first_conn, **request)  # 아직 커밋 전
        second_task = asyncio.create_task(create_text_document(documents_conn, **request))
        await asyncio.sleep(0.5)
        assert not second_task.done()
        await first_conn.commit()

    second = await asyncio.wait_for(second_task, timeout=5)
    assert second["id"] == first["id"]
    assert await document_count(documents_conn) == 1


async def test_a_concurrent_request_proceeds_when_the_first_one_rolls_back(
    migrated_db, documents_conn
):
    """앞 요청이 롤백되면 키도 없으므로 뒤 요청이 문서를 만든다."""
    request = {"title": "동시", "content": "본문", "owner_id": "alice", "idempotency_key": "k-1"}
    async with await psycopg.AsyncConnection.connect(migrated_db) as first_conn:
        await create_text_document(first_conn, **request)
        second_task = asyncio.create_task(create_text_document(documents_conn, **request))
        await asyncio.sleep(0.5)
        await first_conn.rollback()

    second = await asyncio.wait_for(second_task, timeout=5)
    assert await document_count(documents_conn) == 1
    assert await count_rows(documents_conn, "idempotency_keys") == 1
    row = await (await documents_conn.execute("SELECT id FROM documents")).fetchone()
    assert row[0] == second["id"]


async def test_create_without_a_key_records_nothing(documents_conn):
    """키가 없으면 지금처럼 동작한다 — 선택 헤더다."""
    request = {"title": "키 없음", "content": "본문", "owner_id": "alice"}
    await create_text_document(documents_conn, **request)
    await create_text_document(documents_conn, **request)

    assert await document_count(documents_conn) == 2
    assert await count_rows(documents_conn, "idempotency_keys") == 0


FIXTURES = Path(__file__).parent / "fixtures"


async def job_kinds(conn: psycopg.AsyncConnection, document_id) -> list[str]:
    rows = await (
        await conn.execute(
            "SELECT kind FROM embedding_jobs WHERE document_id = %s ORDER BY id",
            (document_id,),
        )
    ).fetchall()
    return [row[0] for row in rows]


async def document_state(conn: psycopg.AsyncConnection, document_id) -> tuple:
    return await (
        await conn.execute(
            "SELECT version, content, extraction_status FROM documents WHERE id = %s",
            (document_id,),
        )
    ).fetchone()


async def text_versions(conn: psycopg.AsyncConnection, document_id) -> list[tuple]:
    return await (
        await conn.execute(
            "SELECT version, content FROM document_versions WHERE document_id = %s"
            " ORDER BY version",
            (document_id,),
        )
    ).fetchall()


async def file_text_versions(conn: psycopg.AsyncConnection, document_id) -> list:
    rows = await (
        await conn.execute(
            "SELECT text_version FROM document_files WHERE document_id = %s"
            " ORDER BY file_version",
            (document_id,),
        )
    ).fetchall()
    return [row[0] for row in rows]


@pytest.mark.parametrize("fixture_name", ["scan_tax_page1.jpg", "scan_tax_pages.pdf"])
async def test_ocr_target_upload_becomes_a_pending_document_with_an_extract_job(
    documents_conn, fixture_name
):
    """스캔 문서는 요청 안에서 OCR하지 않고 「추출 중」 문서로 먼저 생긴다 (ADR-052 결정 3·5)."""
    document = await create_document(
        documents_conn,
        filename=fixture_name,
        data=(FIXTURES / fixture_name).read_bytes(),
        owner_id="alice",
    )

    assert document["extraction_status"] == "pending"
    assert await document_state(documents_conn, document["id"]) == (1, "", "pending")
    assert await text_versions(documents_conn, document["id"]) == []
    assert await file_text_versions(documents_conn, document["id"]) == [None]
    assert await job_kinds(documents_conn, document["id"]) == ["extract"]


async def test_upload_with_text_stays_done_with_an_embed_job(documents_conn):
    document = await create_document(
        documents_conn, filename="guide.md", data="본문".encode(), owner_id="alice"
    )

    assert document["extraction_status"] == "done"
    assert await text_versions(documents_conn, document["id"]) == [(1, "본문")]
    assert await file_text_versions(documents_conn, document["id"]) == [1]
    assert await job_kinds(documents_conn, document["id"]) == ["embed"]


async def test_pdf_with_a_text_layer_stays_done_without_an_extract_job(documents_conn):
    """텍스트 레이어가 있는 PDF는 OCR 대상이 아니다 — 지금처럼 요청 안에서 끝난다 (ADR-052 결정 2)."""
    document = await create_document(
        documents_conn,
        filename="report.pdf",
        data=minimal_pdf("text layer"),
        owner_id="alice",
    )

    assert document["extraction_status"] == "done"
    assert await text_versions(documents_conn, document["id"]) == [(1, "text layer")]
    assert await file_text_versions(documents_conn, document["id"]) == [1]
    assert await job_kinds(documents_conn, document["id"]) == ["embed"]


async def test_same_key_replays_the_pending_scan_document(documents_conn):
    request = {
        "filename": "scan.jpg",
        "data": (FIXTURES / "scan_tax_page1.jpg").read_bytes(),
        "owner_id": "alice",
        "idempotency_key": "scan-1",
    }

    first = await create_document(documents_conn, **request)
    second = await create_document(documents_conn, **request)

    assert second["id"] == first["id"]
    assert second["extraction_status"] == "pending"
    assert await document_count(documents_conn) == 1
    assert await job_kinds(documents_conn, first["id"]) == ["extract"]


async def pending_scan(conn: psycopg.AsyncConnection) -> dict:
    return await create_document(
        conn,
        filename="scan.jpg",
        data=(FIXTURES / "scan_tax_page1.jpg").read_bytes(),
        owner_id="alice",
    )


async def reextracting(conn: psycopg.AsyncConnection, content: str) -> dict:
    """이전 텍스트를 가진 채 추출 중으로 바뀐 문서. 재추출 경로는 step 4라 SQL로 만든다."""
    document = await create_document(
        conn, filename="scan.md", data=content.encode(), owner_id="alice"
    )
    # 첫 임베딩이 끝난 상태로 둔다 — 대기 중이면 새 임베딩 잡이 코얼레싱에 합쳐져 보이지 않는다.
    await conn.execute(
        "UPDATE embedding_jobs SET status = 'done' WHERE document_id = %s", (document["id"],)
    )
    await conn.execute(
        "UPDATE documents SET extraction_status = 'pending' WHERE id = %s", (document["id"],)
    )
    return document


async def test_first_extraction_writes_v1_and_fills_the_original_text_version(
    documents_conn,
):
    document = await pending_scan(documents_conn)

    outcome = await apply_extracted_text(documents_conn, document["id"], "인식된 텍스트")

    assert outcome == "applied"
    assert await document_state(documents_conn, document["id"]) == (
        1,
        "인식된 텍스트",
        "done",
    )
    assert await text_versions(documents_conn, document["id"]) == [(1, "인식된 텍스트")]
    assert await file_text_versions(documents_conn, document["id"]) == [1]
    assert await job_kinds(documents_conn, document["id"]) == ["extract", "embed"]


async def failed_scan_replaced_with(conn: psycopg.AsyncConnection, *replacements: str) -> dict:
    """인식에 실패한 스캔 문서의 원본을 차례로 교체한다. 픽스처 이름마다 새 판이 쌓인다."""
    document = await pending_scan(conn)
    await apply_extracted_text(conn, document["id"], " ")
    for name in replacements:
        version = (await document_state(conn, document["id"]))[0]
        await replace_original_file(
            conn,
            document["id"],
            user_id="alice",
            filename=name,
            data=(FIXTURES / name).read_bytes() if name.startswith("scan_") else b"typed text",
            client_version=version,
        )
    return document


async def test_extraction_fills_only_the_file_version_it_read(documents_conn):
    """인식에 실패했던 판은 텍스트를 낸 적이 없다 — 나중 판의 텍스트 버전을 가리키지 않는다."""
    document = await failed_scan_replaced_with(documents_conn, "scan_tax_pages.pdf")
    assert await file_text_versions(documents_conn, document["id"]) == [None, None]

    await apply_extracted_text(documents_conn, document["id"], "인식된 텍스트")

    assert await file_text_versions(documents_conn, document["id"]) == [None, 1]


async def test_reextraction_leaves_an_earlier_failed_file_version_empty(documents_conn):
    """텍스트 원본으로 교체된 뒤 다시 스캔으로 교체해도, 처음 실패한 판은 비어 있어야 한다."""
    document = await failed_scan_replaced_with(
        documents_conn, "typed.txt", "scan_tax_pages.pdf"
    )
    assert await file_text_versions(documents_conn, document["id"]) == [None, 1, 1]

    await apply_extracted_text(documents_conn, document["id"], "새로 인식된 텍스트")

    assert await file_text_versions(documents_conn, document["id"]) == [None, 1, 1]


async def test_changed_reextraction_appends_a_new_text_version(documents_conn):
    document = await reextracting(documents_conn, "이전 텍스트")

    outcome = await apply_extracted_text(documents_conn, document["id"], "새 텍스트")

    assert outcome == "applied"
    assert await document_state(documents_conn, document["id"]) == (2, "새 텍스트", "done")
    assert await text_versions(documents_conn, document["id"]) == [
        (1, "이전 텍스트"),
        (2, "새 텍스트"),
    ]
    assert await job_kinds(documents_conn, document["id"]) == ["embed", "extract", "embed"]


async def test_unchanged_reextraction_only_marks_done(documents_conn):
    document = await reextracting(documents_conn, "같은 텍스트")

    outcome = await apply_extracted_text(documents_conn, document["id"], "같은 텍스트")

    assert outcome == "unchanged"
    assert await document_state(documents_conn, document["id"]) == (1, "같은 텍스트", "done")
    assert await text_versions(documents_conn, document["id"]) == [(1, "같은 텍스트")]
    assert await job_kinds(documents_conn, document["id"]) == ["embed", "extract"]


@pytest.mark.parametrize("content", [" \t\r\n\f", "가" * (MAX_EXTRACTED_TEXT_LENGTH + 1)])
@pytest.mark.parametrize("previous", ["", "이전 텍스트"])
async def test_blank_or_oversized_result_marks_failed_and_keeps_the_text(
    documents_conn, content, previous
):
    document = (
        await pending_scan(documents_conn)
        if not previous
        else await reextracting(documents_conn, previous)
    )
    jobs_before = await job_kinds(documents_conn, document["id"])
    versions_before = await text_versions(documents_conn, document["id"])

    outcome = await apply_extracted_text(documents_conn, document["id"], content)

    assert outcome == "failed"
    assert await document_state(documents_conn, document["id"]) == (1, previous, "failed")
    assert await text_versions(documents_conn, document["id"]) == versions_before
    assert await job_kinds(documents_conn, document["id"]) == jobs_before


async def test_extraction_for_a_done_or_missing_document_is_skipped(documents_conn):
    document = await create_document(
        documents_conn, filename="guide.md", data="본문".encode(), owner_id="alice"
    )

    assert await apply_extracted_text(documents_conn, document["id"], "덮어쓰기") == "skipped"
    assert await document_state(documents_conn, document["id"]) == (1, "본문", "done")

    failed = await pending_scan(documents_conn)
    await apply_extracted_text(documents_conn, failed["id"], " ")
    assert await apply_extracted_text(documents_conn, failed["id"], "늦은 결과") == "skipped"
    assert await document_state(documents_conn, failed["id"]) == (1, "", "failed")

    assert await apply_extracted_text(documents_conn, uuid4(), "없음") == "skipped"
