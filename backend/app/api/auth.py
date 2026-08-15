from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.api.deps import SESSION_COOKIE, Connection, current_user, require_session_user
from app.api.schemas import (
    AuthStatus,
    CreateTokenRequest,
    LoginRequest,
    TokenCreated,
    TokenSummary,
)
from app.config import get_settings
from app.services import auth as service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=AuthStatus)
async def login(body: LoginRequest, response: Response, conn: Connection) -> AuthStatus:
    try:
        user = await service.authenticate_user(conn, body.username, body.password)
    except service.AuthenticationFailed as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    token = await service.create_session(conn, user["id"])
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        max_age=get_settings().session_lifetime_hours * 60 * 60,
    )
    return AuthStatus(authenticated=True, username=user["username"], is_admin=user["is_admin"])


@router.post("/logout", response_model=AuthStatus)
async def logout(
    response: Response,
    conn: Connection,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> AuthStatus:
    await service.logout(conn, token or "")
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
    )
    return AuthStatus(authenticated=False, username=None, is_admin=False)


@router.get("/me", response_model=AuthStatus)
async def me(user: Annotated[dict | None, Depends(current_user)]) -> AuthStatus:
    if user is None:
        return AuthStatus(authenticated=False, username=None, is_admin=False)
    return AuthStatus(authenticated=True, username=user["username"], is_admin=user["is_admin"])


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest,
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> TokenCreated:
    token = await service.create_token(conn, user["id"], name=body.name, scope=body.scope)
    return TokenCreated.model_validate(token)


@router.get("/tokens", response_model=list[TokenSummary])
async def list_tokens(
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> list[TokenSummary]:
    return [
        TokenSummary.model_validate(token)
        for token in await service.list_tokens(conn, user["id"])
    ]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: UUID,
    conn: Connection,
    user: Annotated[dict, Depends(require_session_user)],
) -> Response:
    try:
        await service.revoke_token(conn, token_id, user_id=user["id"])
    except service.TokenNotFound as error:
        raise HTTPException(status_code=404, detail="토큰을 찾을 수 없습니다.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
