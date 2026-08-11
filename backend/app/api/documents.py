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

from app.api.deps import Connection, require_user_id
from app.api.schemas import (
    DocumentDetail,
    DocumentSummary,
    EditDocumentRequest,
    EditDocumentResponse,
    RelatedResponse,
    TagSuggestionsResponse,
    UpdateTagsRequest,
)
from app.services import documents as service
from app.services.parsing import SUPPORTED_CONTENT_TYPES, UnsupportedFileType
from app.services.related import find_related, suggest_tags
from app.services.search import MAX_K

router = APIRouter(prefix="/api/documents", tags=["documents"])

# 시연 데이터 최대 파일(약 90KB)의 5배보다 충분히 크면서 단일 요청의 메모리 폭증을 막는다.
MAX_UPLOAD_BYTES = 10_000_000
UPLOAD_TOO_LARGE = "업로드 파일은 10MB를 넘을 수 없습니다."


@router.post("", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def upload_document(
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    tags: Annotated[list[str] | None, Form()] = None,
    visibility: Annotated[Literal["public", "private"], Form()] = "public",
) -> DocumentSummary:
    # 선언된 크기를 먼저 보는 것은 큰 파일을 읽지 않기 위해서다. 다만 이 값은 클라이언트가
    # 보내는 것이라 없거나 실제와 다를 수 있으므로, 경계 자체는 읽어들인 바이트로 지킨다.
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
    try:
        document = await service.create_document(
            conn,
            filename=file.filename or "",
            data=data,
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
    user_id: Annotated[str, Depends(require_user_id)],
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
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentDetail:
    document = await service.get_document(conn, document_id, user_id=user_id)
    return DocumentDetail.model_validate(document)


@router.get("/{document_id}/related", response_model=RelatedResponse)
async def get_related(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    k: Annotated[int, Query(ge=1, le=MAX_K)] = 10,
) -> RelatedResponse:
    await service.get_document(conn, document_id, user_id=user_id)
    result = await find_related(conn, document_id=document_id, user_id=user_id, k=k)
    return RelatedResponse.model_validate(result)


@router.get("/{document_id}/tag-suggestions", response_model=TagSuggestionsResponse)
async def get_tag_suggestions(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> TagSuggestionsResponse:
    await service.get_document(conn, document_id, user_id=user_id)
    result = await suggest_tags(
        conn, document_id=document_id, user_id=user_id, limit=limit
    )
    return TagSuggestionsResponse.model_validate(result)


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


@router.put("/{document_id}/tags", response_model=DocumentSummary)
async def update_tags(
    document_id: UUID,
    body: UpdateTagsRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentSummary:
    document = await service.update_tags(
        conn, document_id, user_id=user_id, tags=body.tags
    )
    return DocumentSummary.model_validate(document)


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
