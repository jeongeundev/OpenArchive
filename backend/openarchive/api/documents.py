from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)

from openarchive.api.deps import (
    Connection,
    require_reader,
    require_session_user,
    require_user_id,
    require_write_user_id,
)
from openarchive.api.schemas import (
    BacklinkItem,
    CreateTextDocumentRequest,
    DocumentAccess,
    DocumentCount,
    DocumentDetail,
    DocumentProgress,
    DocumentSummary,
    EditDocumentRequest,
    EditDocumentResponse,
    MoveDocumentRequest,
    ReextractRequest,
    ReextractResponse,
    RelatedResponse,
    ResolvedLinkItem,
    RestoreVersionRequest,
    TagSuggestionsResponse,
    TextVersionDetail,
    TrashItem,
    UpdateAccessRequest,
    UpdateTagsRequest,
    VersionDiff,
)
from openarchive.config import get_settings
from openarchive.services import documents as service
from openarchive.services import trash
from openarchive.services.links import find_backlinks, resolve_links
from openarchive.services.parsing import SUPPORTED_CONTENT_TYPES, UnsupportedFileType
from openarchive.services.related import find_related, suggest_tags
from openarchive.services.search import MAX_K

router = APIRouter(prefix="/api/documents", tags=["documents"])

ContentTypeFilter = Literal[*SUPPORTED_CONTENT_TYPES]

# 문서 생성 요청의 선택 헤더 (ADR-047). 같은 키로 다시 오면 처음 문서를 돌려준다.
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=255)]



async def _read_upload(file: UploadFile) -> bytes:
    """설정된 상한 안에서 업로드 바이트를 읽는다. 넘으면 413이다.

    선언된 크기를 먼저 보는 것은 큰 파일을 읽지 않기 위해서다. 다만 이 값은 클라이언트가
    보내는 것이라 없거나 실제와 다를 수 있으므로, 경계 자체는 읽어들인 바이트로 지킨다.
    """
    mb = get_settings().max_upload_mb
    limit = mb * 1_000_000
    too_large = HTTPException(status_code=413, detail=f"업로드 파일은 {mb}MB를 넘을 수 없습니다.")
    if file.size is not None and file.size > limit:
        raise too_large
    data = await file.read()
    if len(data) > limit:
        raise too_large
    return data


@router.post("", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def upload_document(
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    tags: Annotated[list[str] | None, Form()] = None,
    visibility: Annotated[Literal["public", "private"] | None, Form()] = None,
    folder_id: Annotated[UUID | None, Form()] = None,
    grant_users: Annotated[list[str] | None, Form()] = None,
    grant_groups: Annotated[list[str] | None, Form()] = None,
    idempotency_key: IdempotencyKey = None,
) -> DocumentSummary:
    data = await _read_upload(file)
    try:
        document = await service.create_document(
            conn,
            filename=file.filename or "",
            data=data,
            owner_id=user_id,
            title=title,
            tags=tags,
            visibility=visibility,
            folder_id=folder_id,
            grant_users=grant_users,
            grant_groups=grant_groups,
            idempotency_key=idempotency_key,
        )
    except UnsupportedFileType as error:
        supported = ", ".join(SUPPORTED_CONTENT_TYPES)
        raise HTTPException(status_code=400, detail=f"{error} 지원 형식: {supported}") from error
    except ValueError as error:
        # 파싱 실패(비 UTF-8, 손상된 PDF/DOCX)만 여기 온다. 업로드에만 있는 경로라
        # 앱 전역 핸들러로 올리지 않는다 — 올리면 무관한 ValueError까지 400이 된다.
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentSummary.model_validate(document)


@router.post("/text", response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def create_text_document(
    body: CreateTextDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    idempotency_key: IdempotencyKey = None,
) -> DocumentSummary:
    try:
        document = await service.create_text_document(
            conn,
            title=body.title,
            content=body.content,
            content_type=body.content_type,
            owner_id=user_id,
            tags=body.tags,
            visibility=body.visibility,
            folder_id=body.folder_id,
            grant_users=body.grant_users,
            grant_groups=body.grant_groups,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentSummary.model_validate(document)


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    extraction_status: service.ExtractionStatus | None = None,
    tag: str | None = None,
    q: str | None = None,
    content_type: ContentTypeFilter | None = None,
    folder_id: UUID | None = None,
    sort: Literal["updated", "title"] = "updated",
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentSummary]:
    documents = await service.list_documents(
        conn,
        user_id=user_id,
        embedding_status=status_filter,
        extraction_status=extraction_status,
        tag=tag,
        title_query=q,
        content_type=content_type,
        folder_id=folder_id,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return [DocumentSummary.model_validate(document) for document in documents]


# `/{document_id}`보다 먼저 등록해야 한다 — 뒤에 두면 'progress'가 UUID 검증에 걸린다.
@router.get("/progress", response_model=DocumentProgress)
async def get_document_progress(
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> DocumentProgress:
    return DocumentProgress.model_validate(
        await service.document_progress(conn, user_id=user_id)
    )


# 고정 경로는 문서 UUID 경로보다 먼저 등록한다.
@router.get("/count", response_model=DocumentCount)
async def count_documents(
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    extraction_status: service.ExtractionStatus | None = None,
    tag: str | None = None,
    q: str | None = None,
    content_type: ContentTypeFilter | None = None,
    folder_id: UUID | None = None,
) -> DocumentCount:
    total = await service.count_documents(
        conn,
        user_id=user_id,
        embedding_status=status_filter,
        extraction_status=extraction_status,
        tag=tag,
        title_query=q,
        content_type=content_type,
        folder_id=folder_id,
    )
    return DocumentCount(total=total)


@router.get("/tags", response_model=list[str])
async def list_visible_tags(
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> list[str]:
    return await service.list_visible_tags(conn, user_id=user_id)


@router.get("/trash", response_model=list[TrashItem])
async def list_trash(
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> list[TrashItem]:
    items = await trash.list_trash(
        conn, user_id=user_id, retention_days=get_settings().trash_retention_days
    )
    return [TrashItem.model_validate(item) for item in items]


@router.post("/{document_id}/restore", response_model=DocumentSummary)
async def restore_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> DocumentSummary:
    document = await trash.restore_document(conn, document_id, user_id=user_id)
    return DocumentSummary.model_validate(document)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> DocumentDetail:
    document = await service.get_document(conn, document_id, user_id=user_id)
    return DocumentDetail.model_validate(document)


@router.get("/{document_id}/access", response_model=DocumentAccess)
async def get_document_access(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> DocumentAccess:
    return DocumentAccess.model_validate(
        await service.get_access(conn, document_id, user_id=user_id)
    )


# 세션 전용: 기존 제한 문서의 열람자를 넓히는 관리 행위라 쓰기 토큰에 열지 않는다
# (ADR-044 관리 경로 결정 2). 생성 시 대상 지정은 쓰기 토큰에도 허용한다.
@router.put("/{document_id}/access", response_model=DocumentAccess)
async def update_document_access(
    document_id: UUID,
    body: UpdateAccessRequest,
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> DocumentAccess:
    try:
        access = await service.set_access(
            conn,
            document_id,
            user_id=user["username"],
            visibility=body.visibility,
            users=body.users,
            groups=body.groups,
            follows_folder=body.follows_folder,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentAccess.model_validate(access)


@router.put("/{document_id}/folder", response_model=DocumentDetail)
async def move_document(
    document_id: UUID,
    body: MoveDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> DocumentDetail:
    return DocumentDetail.model_validate(
        await service.move_document(conn, document_id, user_id=user_id, folder_id=body.folder_id)
    )


def _original_file_response(original: dict, *, inline: bool = False) -> Response:
    """원본 내려받기는 attachment, 미리보기는 inline으로 보낸다 (ADR-058).

    앱과 같은 오리진(ADR-041)에서 세션 쿠키를 가진 채 사용자가 올린 HTML·SVG가 렌더링되면
    저장형 XSS가 된다. 미리보기는 허용 목록·CSP sandbox로 제한한다.
    `nosniff`는 브라우저가 내용을 보고 타입을 바꿔 읽는 것을 막는다.
    """
    filename = original["filename"]
    # filename=은 ASCII만 안전하다. 한글 이름은 filename*(RFC 5987)이 나른다.
    fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    disposition = "inline" if inline else "attachment"
    headers = {"Content-Security-Policy": "sandbox; default-src 'none'"} if inline else {}
    return Response(
        content=original["data"],
        media_type=original["media_type"],
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            **headers,
        },
    )


@router.get("/{document_id}/file")
async def download_latest_original(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> Response:
    original = await service.get_original_file(conn, document_id, user_id=user_id)
    return _original_file_response(original)


@router.get("/{document_id}/files/{file_version}")
async def download_original(
    document_id: UUID,
    file_version: Annotated[int, Path(ge=1)],
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> Response:
    original = await service.get_original_file(
        conn, document_id, user_id=user_id, file_version=file_version
    )
    return _original_file_response(original)


@router.get("/{document_id}/files/{file_version}/preview")
async def preview_original(
    document_id: UUID,
    file_version: Annotated[int, Path(ge=1)],
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Response:
    original = await service.get_original_file(
        conn, document_id, user_id=user_id, file_version=file_version, preview=True
    )
    return _original_file_response(original, inline=True)


@router.get("/{document_id}/links", response_model=list[ResolvedLinkItem])
async def get_document_links(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> list[ResolvedLinkItem]:
    return [
        ResolvedLinkItem.model_validate(item)
        for item in await resolve_links(conn, document_id=document_id, user_id=user_id)
    ]


@router.get("/{document_id}/backlinks", response_model=list[BacklinkItem])
async def get_document_backlinks(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
) -> list[BacklinkItem]:
    return [
        BacklinkItem.model_validate(item)
        for item in await find_backlinks(conn, document_id=document_id, user_id=user_id)
    ]


@router.get("/{document_id}/related", response_model=RelatedResponse)
async def get_related(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
    k: Annotated[int, Query(ge=1, le=MAX_K)] = 10,
) -> RelatedResponse:
    result = await find_related(conn, document_id=document_id, user_id=user_id, k=k)
    return RelatedResponse.model_validate(result)


@router.get("/{document_id}/tag-suggestions", response_model=TagSuggestionsResponse)
async def get_tag_suggestions(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> TagSuggestionsResponse:
    result = await suggest_tags(
        conn, document_id=document_id, user_id=user_id, limit=limit
    )
    return TagSuggestionsResponse.model_validate(result)


@router.get("/{document_id}/versions/{version}", response_model=TextVersionDetail)
async def get_document_version(
    document_id: UUID,
    version: Annotated[int, Path(ge=1)],
    conn: Connection,
    user_id: Annotated[str, Depends(require_reader)],
    chunk: Annotated[int | None, Query(ge=0)] = None,
) -> TextVersionDetail:
    document_version = await service.get_document_version(
        conn, document_id, version=version, user_id=user_id, chunk=chunk
    )
    return TextVersionDetail.model_validate(document_version)


@router.get("/{document_id}/versions/{base}/diff/{target}", response_model=VersionDiff)
async def diff_document_versions(
    document_id: UUID,
    base: Annotated[int, Path(ge=1)],
    target: Annotated[int, Path(ge=1)],
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> VersionDiff:
    result = await service.diff_versions(
        conn, document_id, user_id=user_id, base=base, target=target
    )
    return VersionDiff.model_validate(result)


@router.post(
    "/{document_id}/versions/{version}/restore", response_model=EditDocumentResponse
)
async def restore_document_version(
    document_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: RestoreVersionRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> EditDocumentResponse:
    document = await service.restore_version(
        conn,
        document_id,
        version=version,
        user_id=user_id,
        client_version=body.current_version,
    )
    return EditDocumentResponse.model_validate(document)


@router.put("/{document_id}", response_model=EditDocumentResponse)
async def edit_document(
    document_id: UUID,
    body: EditDocumentRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> EditDocumentResponse:
    document = await service.update_extracted_text(
        conn,
        document_id,
        user_id=user_id,
        content=body.content,
        client_version=body.version,
    )
    return EditDocumentResponse.model_validate(document)


@router.put("/{document_id}/file", response_model=DocumentSummary)
async def replace_original_file(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    file: Annotated[UploadFile, File()],
    current_version: Annotated[int, Form()],
) -> DocumentSummary:
    data = await _read_upload(file)
    try:
        document = await service.replace_original_file(
            conn,
            document_id,
            user_id=user_id,
            filename=file.filename or "",
            data=data,
            client_version=current_version,
        )
    except UnsupportedFileType as error:
        supported = ", ".join(SUPPORTED_CONTENT_TYPES)
        raise HTTPException(status_code=400, detail=f"{error} 지원 형식: {supported}") from error
    except ValueError as error:
        # 파싱 실패만 여기 온다 — 업로드와 같은 이유로 전역 핸들러에 올리지 않는다.
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentSummary.model_validate(document)


@router.post("/{document_id}/reextract", response_model=ReextractResponse)
async def reextract_document(
    document_id: UUID,
    body: ReextractRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> ReextractResponse:
    try:
        document, changed = await service.reextract_document(
            conn, document_id, user_id=user_id, client_version=body.current_version
        )
    except UnsupportedFileType as error:
        supported = ", ".join(SUPPORTED_CONTENT_TYPES)
        raise HTTPException(status_code=400, detail=f"{error} 지원 형식: {supported}") from error
    except ValueError as error:
        # 파싱 실패만 여기 온다 — 업로드와 같은 이유로 전역 핸들러에 올리지 않는다.
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ReextractResponse.model_validate({**document, "changed": changed})


@router.put("/{document_id}/tags", response_model=DocumentSummary)
async def update_tags(
    document_id: UUID,
    body: UpdateTagsRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> DocumentSummary:
    document = await service.update_tags(
        conn, document_id, user_id=user_id, tags=body.tags
    )
    return DocumentSummary.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    permanent: bool = False,
) -> Response:
    if permanent:
        await trash.purge_document(conn, document_id, user_id=user_id)
    else:
        await trash.trash_document(conn, document_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/reembed", response_model=DocumentSummary)
async def reembed_document(
    document_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
) -> DocumentSummary:
    document = await service.request_reembedding(conn, document_id, user_id=user_id)
    return DocumentSummary.model_validate(document)
