import hashlib
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from app.api.deps import get_conn, optional_user_id, require_user_id
from app.api.schemas import (
    DocumentDetail,
    DocumentSummary,
    EditDocumentRequest,
    EditDocumentResponse,
    TextVersion,
)
from app.services.parsing import (
    SUPPORTED_CONTENT_TYPES,
    TextDecodeError,
    UnsupportedFileType,
    detect_content_type,
    extract_text,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])
Connection = Annotated[psycopg.AsyncConnection, Depends(get_conn)]


async def _require_owner(conn: psycopg.AsyncConnection, document_id: UUID, user_id: str) -> None:
    row = await (
        await conn.execute(
            "SELECT owner_id, visibility FROM documents WHERE id = %s", (document_id,)
        )
    ).fetchone()
    if row is None or (row[1] == "private" and row[0] != user_id):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    if row[0] != user_id:
        raise HTTPException(status_code=403, detail="문서를 수정할 권한이 없습니다.")


@router.post("", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def upload_document(
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    tags: Annotated[list[str] | None, Form()] = None,
    visibility: Annotated[Literal["public", "private"], Form()] = "public",
) -> DocumentSummary:
    filename = file.filename or ""
    try:
        content_type = detect_content_type(filename)
        content = extract_text(await file.read(), content_type)
    except UnsupportedFileType as error:
        supported = ", ".join(SUPPORTED_CONTENT_TYPES)
        raise HTTPException(status_code=400, detail=f"{error} 지원 형식: {supported}") from error
    except (TextDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not content.strip():
        raise HTTPException(
            status_code=400,
            detail="문서에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다.",
        )

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        INSERT INTO documents
            (title, filename, content_type, content, content_hash, owner_id, visibility, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, title, filename, content_type, version, owner_id, visibility, tags,
                  embedding_status, created_at, updated_at
        """,
        (
            title or PurePath(filename).stem,
            filename,
            content_type,
            content,
            content_hash,
            user_id,
            visibility,
            tags or [],
        ),
    )
    return DocumentSummary.model_validate(await cur.fetchone())


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    tag: str | None = None,
) -> list[DocumentSummary]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT id, title, filename, content_type, version, owner_id, visibility, tags,
               embedding_status, created_at, updated_at
        FROM documents
        WHERE (visibility = 'public' OR owner_id = %(user)s)
          AND (%(status)s::text IS NULL OR embedding_status = %(status)s)
          AND (%(tag)s::text IS NULL OR %(tag)s = ANY(tags))
        ORDER BY created_at DESC, id
        """,
        {"user": user_id, "status": status_filter, "tag": tag},
    )
    return [DocumentSummary.model_validate(row) for row in await cur.fetchall()]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
) -> DocumentDetail:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        SELECT id, title, filename, content_type, content, version, owner_id, visibility, tags,
               embedding_status, created_at, updated_at,
               (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_count,
               (SELECT min(version) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_version
        FROM documents d
        WHERE id = %(id)s
          AND (visibility = 'public' OR owner_id = %(user)s)
        """,
        {"id": document_id, "user": user_id},
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    await cur.execute(
        """
        SELECT version, created_at
        FROM document_versions
        WHERE document_id = %s
        ORDER BY version
        """,
        (document_id,),
    )
    row["versions"] = [TextVersion.model_validate(version) for version in await cur.fetchall()]
    return DocumentDetail.model_validate(row)


@router.put("/{document_id}", response_model=EditDocumentResponse)
async def edit_document(
    document_id: UUID,
    body: EditDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> EditDocumentResponse:
    await _require_owner(conn, document_id, user_id)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="추출 텍스트는 비어 있을 수 없습니다.")

    content_hash = hashlib.sha256(body.content.encode("utf-8")).hexdigest()
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        UPDATE documents
           SET version = version + 1, content = %(content)s, content_hash = %(hash)s,
               updated_at = now()
         WHERE id = %(id)s AND version = %(client_version)s
        RETURNING id, title, filename, content_type, content, version, owner_id, visibility,
                  tags, embedding_status, created_at, updated_at
        """,
        {
            "id": document_id,
            "content": body.content,
            "hash": content_hash,
            "client_version": body.version,
        },
    )
    row = await cur.fetchone()
    if row is None:
        await cur.execute("SELECT version FROM documents WHERE id = %s", (document_id,))
        current_version = (await cur.fetchone())["version"]
        return JSONResponse(
            status_code=409,
            content={
                "detail": "다른 곳에서 문서가 수정되었습니다. 새로고침 후 다시 시도하세요.",
                "current_version": current_version,
            },
        )
    return EditDocumentResponse.model_validate(row)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Response:
    await _require_owner(conn, document_id, user_id)
    await conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/reembed", response_model=DocumentSummary)
async def reembed_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentSummary:
    await _require_owner(conn, document_id, user_id)
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        """
        UPDATE documents SET content_hash = content_hash WHERE id = %s
        RETURNING id, title, filename, content_type, version, owner_id, visibility, tags,
                  embedding_status, created_at, updated_at
        """,
        (document_id,),
    )
    return DocumentSummary.model_validate(await cur.fetchone())
