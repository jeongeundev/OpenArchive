from typing import Annotated, Literal
from uuid import UUID

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

from app.api.deps import Connection, optional_user_id, require_user_id
from app.api.schemas import (
    DocumentDetail,
    DocumentSummary,
    EditDocumentRequest,
    EditDocumentResponse,
)
from app.services import documents as service
from app.services.parsing import SUPPORTED_CONTENT_TYPES, UnsupportedFileType

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def upload_document(
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    tags: Annotated[list[str] | None, Form()] = None,
    visibility: Annotated[Literal["public", "private"], Form()] = "public",
) -> DocumentSummary:
    try:
        document = await service.create_document(
            conn,
            filename=file.filename or "",
            data=await file.read(),
            owner_id=user_id,
            title=title,
            tags=tags,
            visibility=visibility,
        )
    except UnsupportedFileType as error:
        supported = ", ".join(SUPPORTED_CONTENT_TYPES)
        raise HTTPException(status_code=400, detail=f"{error} 지원 형식: {supported}") from error
    except ValueError as error:
        # 파싱 실패(비 UTF-8, 손상된 PDF/DOCX)만 여기 온다. 업로드에만 있는 경로라
        # 앱 전역 핸들러로 올리지 않는다 — 올리면 무관한 ValueError까지 400이 된다.
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentSummary.model_validate(document)


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    tag: str | None = None,
) -> list[DocumentSummary]:
    documents = await service.list_documents(
        conn, user_id=user_id, embedding_status=status_filter, tag=tag
    )
    return [DocumentSummary.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str | None, Depends(optional_user_id)],
) -> DocumentDetail:
    document = await service.get_document(conn, document_id, user_id=user_id)
    return DocumentDetail.model_validate(document)


@router.put("/{document_id}", response_model=EditDocumentResponse)
async def edit_document(
    document_id: UUID,
    body: EditDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> EditDocumentResponse:
    document = await service.update_extracted_text(
        conn,
        document_id,
        user_id=user_id,
        content=body.content,
        client_version=body.version,
    )
    return EditDocumentResponse.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Response:
    await service.delete_document(conn, document_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/reembed", response_model=DocumentSummary)
async def reembed_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentSummary:
    document = await service.request_reembedding(conn, document_id, user_id=user_id)
    return DocumentSummary.model_validate(document)
