from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from openarchive.api.deps import Connection, require_admin, require_user_id
from openarchive.api.schemas import CreateGroupRequest, GroupSummary, Principals
from openarchive.services import grants as service
from openarchive.services.auth import UserNotFound

# 그룹 관리는 세션 전용이다 — 관리자의 토큰도 넘지 못한다 (ADR-034 결정 6)
router = APIRouter(
    prefix="/api/admin/groups",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

principals_router = APIRouter(prefix="/api/principals", tags=["principals"])


def _not_found(error: Exception) -> HTTPException:
    if isinstance(error, service.GroupNotFound):
        return HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")
    return HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")


@router.post("", response_model=GroupSummary, status_code=status.HTTP_201_CREATED)
async def create_group(body: CreateGroupRequest, conn: Connection) -> GroupSummary:
    try:
        group = await service.create_group(conn, body.name)
    except service.GroupAlreadyExists as error:
        raise HTTPException(status_code=409, detail="이미 존재하는 그룹 이름입니다.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return GroupSummary.model_validate(group)


@router.get("", response_model=list[GroupSummary])
async def list_groups(conn: Connection) -> list[GroupSummary]:
    return [GroupSummary.model_validate(group) for group in await service.list_groups(conn)]


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: UUID, conn: Connection) -> Response:
    try:
        await service.delete_group(conn, group_id)
    except service.GroupNotFound as error:
        raise _not_found(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{group_id}/members/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def add_member(group_id: UUID, username: str, conn: Connection) -> Response:
    try:
        await service.add_member(conn, group_id, username)
    except (service.GroupNotFound, UserNotFound) as error:
        raise _not_found(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{group_id}/members/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(group_id: UUID, username: str, conn: Connection) -> Response:
    try:
        await service.remove_member(conn, group_id, username)
    except (service.GroupNotFound, UserNotFound) as error:
        raise _not_found(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@principals_router.get("", response_model=Principals)
async def list_principals(
    conn: Connection, _user_id: Annotated[str, Depends(require_user_id)]
) -> Principals:
    return Principals.model_validate(await service.list_principals(conn))
