from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from app.api.deps import SESSION_COOKIE, Connection, current_user
from app.api.schemas import AuthStatus, LoginRequest
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
