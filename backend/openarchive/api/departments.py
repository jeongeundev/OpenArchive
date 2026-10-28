# Copyright 2026 jeongeundev
# SPDX-License-Identifier: MIT
"""부서 디렉터리와 세션 전용 위임 관리."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from openarchive.api.deps import Connection, current_user, require_session_user, require_user_id
from openarchive.api.schemas import Department, UpdateDepartmentRequest
from openarchive.services import departments as service

router = APIRouter(prefix="/api/departments", tags=["departments"])
SessionUser = Annotated[dict, Depends(require_session_user)]


async def _is_admin(user: Annotated[dict | None, Depends(current_user)]) -> bool:
    # 인증과 공유 주체 거부는 require_user_id가 담당한다.
    return user is not None and user["is_admin"]


@router.get("", response_model=list[Department])
async def list_departments(
    conn: Connection,
    user_id: Annotated[str, Depends(require_user_id)],
    is_admin: Annotated[bool, Depends(_is_admin)],
) -> list[Department]:
    return [Department.model_validate(row) for row in await service.list_departments(
        conn, user_id=user_id, is_admin=is_admin
    )]


@router.patch("/{group_id}", status_code=204)
async def update_department(
    group_id: UUID, body: UpdateDepartmentRequest, conn: Connection, user: SessionUser
) -> Response:
    await service.update_department(
        conn, group_id, description=body.description,
        user_id=user["username"], is_admin=user["is_admin"]
    )
    return Response(status_code=204)


@router.put("/{group_id}/members/{username}", status_code=204)
async def add_member(
    group_id: UUID, username: str, conn: Connection, user: SessionUser
) -> Response:
    await service.add_department_member(
        conn, group_id, username, user_id=user["username"], is_admin=user["is_admin"]
    )
    return Response(status_code=204)


@router.delete("/{group_id}/members/{username}", status_code=204)
async def remove_member(
    group_id: UUID, username: str, conn: Connection, user: SessionUser
) -> Response:
    await service.remove_department_member(
        conn, group_id, username, user_id=user["username"], is_admin=user["is_admin"]
    )
    return Response(status_code=204)


@router.put("/{group_id}/managers/{username}", status_code=204)
async def add_manager(
    group_id: UUID, username: str, conn: Connection, user: SessionUser
) -> Response:
    await service.set_department_manager(
        conn, group_id, username, True, user_id=user["username"], is_admin=user["is_admin"]
    )
    return Response(status_code=204)


@router.delete("/{group_id}/managers/{username}", status_code=204)
async def remove_manager(
    group_id: UUID, username: str, conn: Connection, user: SessionUser
) -> Response:
    await service.set_department_manager(
        conn, group_id, username, False, user_id=user["username"], is_admin=user["is_admin"]
    )
    return Response(status_code=204)
