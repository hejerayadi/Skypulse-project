"""
The HTTP surface: /api/auth/*.

This layer does no deciding. It reads the request, calls service.py, writes the
session cookies and turns errors into status codes — nothing more, so the rules
stay in one place.

Endpoints
    POST   /api/auth/register          create an account and sign in
    POST   /api/auth/login             sign in
    POST   /api/auth/refresh           renew the session
    POST   /api/auth/logout            end this session
    POST   /api/auth/logout-all        end every session for this account
    GET    /api/auth/me                the signed-in user
    PATCH  /api/auth/me                edit profile and preferences
    POST   /api/auth/me/password       change password (ends other sessions)
    POST   /api/auth/forgot-password   email a reset link
    GET    /api/auth/users             admin: list accounts
    PATCH  /api/auth/users/{id}        admin: change role or status
    DELETE /api/auth/users/{id}        admin: delete an account
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.authentification import config, service
from app.authentification.dependencies import (
    extract_access_token,
    extract_refresh_token,
    get_current_user,
    require_admin,
)
from app.authentification.gotrue import SupabaseAuthError, SupabaseUnavailable
from app.authentification.models import (
    AdminUpdateUserRequest,
    AuthSession,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    UpdateProfileRequest,
    UserAccount,
    UserListResponse,
    UserPublic,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ==========================================================
# Plumbing
# ==========================================================

def _set_session_cookies(response: Response, session: AuthSession) -> None:
    """
    Write the Supabase tokens as httpOnly cookies.

    This is the main thing the backend adds over calling Supabase from the
    browser: httpOnly means no script on the page can read the session, so an
    XSS bug cannot walk off with it the way it could from localStorage.
    """

    if session.tokens is None:
        return

    response.set_cookie(
        key=config.ACCESS_COOKIE_NAME,
        value=session.tokens.access_token,
        max_age=config.ACCESS_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        domain=config.COOKIE_DOMAIN,
        path="/",
    )

    if session.tokens.refresh_token:
        response.set_cookie(
            key=config.REFRESH_COOKIE_NAME,
            value=session.tokens.refresh_token,
            max_age=config.REFRESH_COOKIE_TTL_SECONDS,
            httponly=True,
            secure=config.COOKIE_SECURE,
            samesite=config.COOKIE_SAMESITE,
            domain=config.COOKIE_DOMAIN,
            # Scoped to the auth routes, so the refresh token isn't attached to
            # every telemetry request that goes past.
            path=config.REFRESH_COOKIE_PATH,
        )


def _clear_session_cookies(response: Response) -> None:
    # delete_cookie has to repeat path/domain exactly — a mismatch leaves the
    # original cookie in place and the browser stays "signed in".
    response.delete_cookie(
        key=config.ACCESS_COOKIE_NAME,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        domain=config.COOKIE_DOMAIN,
        path="/",
    )
    response.delete_cookie(
        key=config.REFRESH_COOKIE_NAME,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        domain=config.COOKIE_DOMAIN,
        path=config.REFRESH_COOKIE_PATH,
    )


async def _call(function, *args, **kwargs):
    """
    Run a service call off the event loop and map its errors onto HTTP.

    Everything downstream is blocking network I/O to Supabase, so calling it
    inline would stall telemetry ingest for the duration of every sign-in.
    """

    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except SupabaseUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except service.AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ==========================================================
# Sign in / sign out
# ==========================================================

@router.post("/register", response_model=AuthSession, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, response: Response):
    session = await _call(service.register, payload)
    # No cookies when Supabase is waiting on the confirmation email — there is
    # no session to store yet.
    _set_session_cookies(response, session)
    return session


@router.post("/login", response_model=AuthSession)
async def login(payload: LoginRequest, response: Response):
    session = await _call(service.login, payload.email, payload.password)
    _set_session_cookies(response, session)
    return session


@router.post("/refresh", response_model=AuthSession)
async def refresh(request: Request, response: Response, payload: RefreshRequest | None = None):
    token = extract_refresh_token(request) or (payload.refresh_token if payload else None)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token supplied",
        )

    try:
        session = await _call(service.refresh_session, token)
    except HTTPException:
        # A refresh that fails means this browser's session is finished; leaving
        # the dead cookies in place would make every later request retry it.
        _clear_session_cookies(response)
        raise

    _set_session_cookies(response, session)
    return session


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response):
    await _call(service.logout, extract_access_token(request), False)
    _clear_session_cookies(response)
    return MessageResponse(message="Signed out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    request: Request,
    response: Response,
    _: UserAccount = Depends(get_current_user),
):
    await _call(service.logout, extract_access_token(request), True)
    _clear_session_cookies(response)
    return MessageResponse(message="Signed out of every device")


# ==========================================================
# The signed-in account
# ==========================================================

@router.get("/me", response_model=UserPublic)
def read_me(user: UserAccount = Depends(get_current_user)):
    return user.to_public()


@router.patch("/me", response_model=UserPublic)
async def update_me(
    payload: UpdateProfileRequest,
    user: UserAccount = Depends(get_current_user),
):
    updated = await _call(service.update_own_account, user, payload)
    return updated.to_public()


@router.post("/me/password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: UserAccount = Depends(get_current_user),
):
    await _call(
        service.change_password,
        user,
        extract_access_token(request),
        payload.current_password,
        payload.new_password,
    )
    # Every session is revoked, this one included, so the cookies here are
    # already dead — clear them and make the caller sign in again.
    _clear_session_cookies(response)
    return MessageResponse(message="Password changed. Please sign in again.")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    await _call(service.send_password_reset, str(payload.email))
    # Deliberately the same answer whether or not the address exists.
    return MessageResponse(
        message="If that address has an account, a reset link is on its way."
    )


# ==========================================================
# Administration
# ==========================================================

@router.get("/users", response_model=UserListResponse)
async def list_users(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: UserAccount = Depends(require_admin),
):
    users, total = await _call(service.list_users, limit, offset)
    return UserListResponse(users=users, total=total)


@router.patch("/users/{user_id}", response_model=UserPublic)
async def admin_update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    actor: UserAccount = Depends(require_admin),
):
    updated = await _call(service.admin_update_user, actor, user_id, payload)
    return updated.to_public()


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def admin_delete_user(user_id: str, actor: UserAccount = Depends(require_admin)):
    await _call(service.admin_delete_user, actor, user_id)
    return MessageResponse(message="Account deleted")
