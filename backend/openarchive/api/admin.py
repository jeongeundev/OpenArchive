# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from openarchive.api.deps import Connection, require_admin
from openarchive.api.schemas import CreateUserRequest, UpdateUserDepartmentRequest, UserSummary
from openarchive.services import auth as service
from openarchive.services.grants import GroupNotFound

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, conn: Connection) -> UserSummary:
    try:
        user = await service.create_user(
            conn,
            body.username,
            body.password,
            is_admin=body.is_admin,
            department_group_id=body.department_group_id,
        )
    except GroupNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except service.UserAlreadyExists as error:
        raise HTTPException(status_code=409, detail="이미 존재하는 사용자명입니다.") from error
    except (service.PasswordTooShort, service.InvalidUsername) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return UserSummary.model_validate(user)


@router.get("", response_model=list[UserSummary])
async def list_users(conn: Connection) -> list[UserSummary]:
    return [UserSummary.model_validate(user) for user in await service.list_users(conn)]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    conn: Connection,
    admin: Annotated[dict, Depends(require_admin)],
    transfer_to: str | None = None,
) -> Response:
    try:
        await service.delete_user(
            conn, user_id, transfer_to=transfer_to, actor=admin["username"]
        )
    except service.CannotDeleteSelf as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except service.UserOwnsDocuments as error:
        raise HTTPException(
            status_code=409,
            detail="소유한 문서나 만든 폴더가 있어 삭제할 수 없습니다.",
        ) from error
    except service.UserNotFound as error:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{user_id}/department", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_department(
    user_id: UUID, body: UpdateUserDepartmentRequest, conn: Connection
) -> Response:
    try:
        await service.set_user_department(conn, user_id, body.group_id)
    except service.UserNotFound as error:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.") from error
    except GroupNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
