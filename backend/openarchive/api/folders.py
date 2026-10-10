from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from openarchive.api.deps import (
    Connection,
    current_user,
    require_reader,
    require_session_user,
    require_user_id,
    require_write_user_id,
)
from openarchive.api.schemas import (
    CreateFolderRequest,
    Folder,
    FolderOwnerTransferred,
    FolderScope,
    RenameFolderRequest,
    SharedFolder,
    TransferOwnerRequest,
    UpdateFolderAccessRequest,
)
from openarchive.services import folders as service
from openarchive.services.visibility import SHARE_PRINCIPAL_PREFIX

router = APIRouter(prefix="/api/folders", tags=["folders"])


async def _is_admin(user: Annotated[dict | None, Depends(current_user)]) -> bool:
    # 인증은 각 경로의 require_user_id/require_write_user_id가 담당한다.
    return user is not None and user["is_admin"]


async def _folder_response(conn, folder_id, user_id, is_admin):
    # 생성·이름 변경의 원시 행에 조회 서비스가 제공하는 실효 범위·집계·관리 정보를 붙인다.
    rows = await service.list_folders(conn, user_id=user_id, is_admin=is_admin)
    return Folder.model_validate(next(row for row in rows if row["id"] == folder_id))


# 공유 허용 목록의 읽기 경로다 (ADR-044 「공유」 결정 5, #206). 공유 주체에게는 공유에 넣은
# 폴더와 그 하위만 SharedFolder로 준다 — 범위·만든 사람이 섞이지 않게 응답 모델을 가른다.
@router.get("", response_model=list[Folder] | list[SharedFolder])
async def list_folders(
    conn: Connection,
    principal: Annotated[str, Depends(require_reader)],
    is_admin: Annotated[bool, Depends(_is_admin)],
) -> list[Folder] | list[SharedFolder]:
    rows = await service.list_folders(conn, user_id=principal, is_admin=is_admin)
    model = SharedFolder if principal.startswith(SHARE_PRINCIPAL_PREFIX) else Folder
    return [model.model_validate(row) for row in rows]


@router.post("", response_model=Folder, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: CreateFolderRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    is_admin: Annotated[bool, Depends(_is_admin)],
) -> Folder:
    try:
        row = await service.create_folder(
            conn, user_id=user_id, name=body.name, parent_id=body.parent_id
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await _folder_response(conn, row["id"], user_id, is_admin)


@router.patch("/{folder_id}", response_model=Folder)
async def rename_folder(
    folder_id: UUID,
    body: RenameFolderRequest,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    is_admin: Annotated[bool, Depends(_is_admin)],
) -> Folder:
    try:
        row = await service.rename_folder(
            conn, folder_id, user_id=user_id, is_admin=is_admin, name=body.name
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await _folder_response(conn, row["id"], user_id, is_admin)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_write_user_id)],
    is_admin: Annotated[bool, Depends(_is_admin)],
) -> Response:
    await service.delete_folder(conn, folder_id, user_id=user_id, is_admin=is_admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{folder_id}/access", response_model=FolderScope)
async def get_folder_access(
    folder_id: UUID,
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
) -> FolderScope:
    return FolderScope.model_validate(
        await service.get_folder_access(conn, folder_id, user_id=user_id)
    )


@router.put("/{folder_id}/access", response_model=FolderScope)
async def set_folder_access(
    folder_id: UUID,
    body: UpdateFolderAccessRequest,
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> FolderScope:
    return FolderScope.model_validate(
        await service.set_folder_access(
            conn,
            folder_id,
            user_id=user["username"],
            visibility=body.visibility,
            users=body.users,
            groups=body.groups,
        )
    )


# 세션 전용: 소유자 이전은 열람 범위 변경과 같은 관리 경계(ADR-034 결정 6, ADR-061 결정 2).
@router.put("/{folder_id}/owner", response_model=FolderOwnerTransferred)
async def transfer_owner(
    folder_id: UUID,
    body: TransferOwnerRequest,
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> FolderOwnerTransferred:
    return FolderOwnerTransferred.model_validate(
        await service.transfer_folder_owner(
            conn, folder_id, user_id=user["username"], new_owner=body.owner
        )
    )
