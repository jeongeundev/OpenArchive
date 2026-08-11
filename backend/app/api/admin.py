from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import Connection, require_admin
from app.api.schemas import CreateUserRequest, UserSummary
from app.services import auth as service

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, conn: Connection) -> UserSummary:
    try:
        user = await service.create_user(
            conn, body.username, body.password, is_admin=body.is_admin
        )
    except service.UserAlreadyExists as error:
        raise HTTPException(status_code=409, detail="이미 존재하는 사용자명입니다.") from error
    return UserSummary.model_validate(user)


@router.get("", response_model=list[UserSummary])
async def list_users(conn: Connection) -> list[UserSummary]:
    return [UserSummary.model_validate(user) for user in await service.list_users(conn)]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, conn: Connection) -> Response:
    try:
        await service.delete_user(conn, user_id)
    except service.UserOwnsDocuments as error:
        raise HTTPException(
            status_code=409,
            detail="소유 문서가 있는 사용자는 삭제할 수 없습니다. 문서를 먼저 삭제하세요.",
        ) from error
    except service.UserNotFound as error:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
