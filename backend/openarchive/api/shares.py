from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from openarchive.api.deps import Connection, require_session_user
from openarchive.api.schemas import (
    CreateShareRequest,
    CreateShareTokenRequest,
    ShareSummary,
    TokenCreated,
)
from openarchive.services import shares as service
from openarchive.services.auth import InvalidTokenExpiry, TokenNotFound
from openarchive.services.folders import FolderNotFound

# 공유 관리는 세션 전용이다 — 토큰이 공유 토큰을 발급하면 폐기 뒤에도
# 자격증명을 재생할 수 있다 (ADR-034 결정 6, ADR-044 「공유」)
router = APIRouter(prefix="/api/shares", tags=["shares"])

SessionUser = Annotated[dict, Depends(require_session_user)]


def _share_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="공유를 찾을 수 없습니다.")


def _folder_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="폴더를 찾을 수 없습니다.")


@router.post("", response_model=ShareSummary, status_code=status.HTTP_201_CREATED)
async def create_share(
    body: CreateShareRequest, conn: Connection, user: SessionUser
) -> ShareSummary:
    try:
        share = await service.create_share(conn, owner=user["username"], name=body.name)
    except service.ShareAlreadyExists as error:
        raise HTTPException(status_code=409, detail="이미 존재하는 공유 이름입니다.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ShareSummary.model_validate(share)


@router.get("", response_model=list[ShareSummary])
async def list_shares(conn: Connection, user: SessionUser) -> list[ShareSummary]:
    return [
        ShareSummary.model_validate(share)
        for share in await service.list_shares(conn, owner=user["username"])
    ]


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(share_id: UUID, conn: Connection, user: SessionUser) -> Response:
    try:
        await service.delete_share(conn, share_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{share_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_document(
    share_id: UUID, document_id: UUID, conn: Connection, user: SessionUser
) -> Response:
    try:
        await service.add_document(conn, share_id, document_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{share_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(
    share_id: UUID, document_id: UUID, conn: Connection, user: SessionUser
) -> Response:
    try:
        await service.remove_document(conn, share_id, document_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 폴더 넣기·빼기 — 최상위 폴더를 만든 공유 소유자만 (#206). 폴더를 볼 수 없으면 404,
# 최상위 폴더를 만든 사람이 아니면 403(NotFolderCreator는 main.py가 옮긴다).
@router.put("/{share_id}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_folder(
    share_id: UUID, folder_id: UUID, conn: Connection, user: SessionUser
) -> Response:
    try:
        await service.add_folder(conn, share_id, folder_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    except FolderNotFound as error:
        raise _folder_not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{share_id}/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_folder(
    share_id: UUID, folder_id: UUID, conn: Connection, user: SessionUser
) -> Response:
    try:
        await service.remove_folder(conn, share_id, folder_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    except FolderNotFound as error:
        raise _folder_not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{share_id}/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED
)
async def issue_token(
    share_id: UUID, body: CreateShareTokenRequest, conn: Connection, user: SessionUser
) -> TokenCreated:
    try:
        token = await service.issue_share_token(
            conn, share_id, owner=user["username"], name=body.name, expires_at=body.expires_at
        )
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    except InvalidTokenExpiry as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return TokenCreated.model_validate(token)


@router.delete("/{share_id}/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    share_id: UUID, token_id: UUID, conn: Connection, user: SessionUser
) -> Response:
    try:
        await service.revoke_share_token(conn, share_id, token_id, owner=user["username"])
    except service.ShareNotFound as error:
        raise _share_not_found() from error
    except TokenNotFound as error:
        raise HTTPException(status_code=404, detail="토큰을 찾을 수 없습니다.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
