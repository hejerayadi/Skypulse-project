"""
The dependencies any router puts on an endpoint to require a signed-in user.

    @router.get("/nodes")
    def get_nodes(user: UserAccount = Depends(get_current_user)):
        ...

    @router.delete("/nodes/{id}")
    def drop_node(user: UserAccount = Depends(require_admin)):
        ...

Credentials are read from the httpOnly cookie first and the Authorization
header second, so browsers need to send nothing explicitly while scripts and
Node-RED can still use a bearer token.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.authentification import config, service
from app.authentification.gotrue import SupabaseAuthError, SupabaseUnavailable
from app.authentification.models import UserAccount, UserRole


def extract_access_token(request: Request) -> str | None:
    token = request.cookies.get(config.ACCESS_COOKIE_NAME)
    if token:
        return token

    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip() or None

    return None


def extract_refresh_token(request: Request) -> str | None:
    token = request.cookies.get(config.REFRESH_COOKIE_NAME)
    if token:
        return token

    header = request.headers.get("x-refresh-token")
    return header.strip() if header else None


def get_current_user(request: Request) -> UserAccount:
    token = extract_access_token(request)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in",
            # Tells the caller how to authenticate, and is what turns a bare 401
            # into something an HTTP client can act on.
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return service.resolve_access_token(token)
    except SupabaseUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except service.AuthError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.message,
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_optional_user(request: Request) -> UserAccount | None:
    """For endpoints that behave differently when signed in but don't require it."""

    try:
        return get_current_user(request)
    except HTTPException:
        return None


def require_role(minimum: UserRole):
    """
    Role gate built on the USER < OPERATOR < ADMIN ordering, so
    require_role(OPERATOR) also admits admins without listing them.

    The role comes from public.profiles, never from the token — Supabase's own
    `role` claim is the Postgres role ("authenticated"), not this one.
    """

    def dependency(user: UserAccount = Depends(get_current_user)) -> UserAccount:
        if not user.has_at_least(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {minimum.value} role or higher",
            )
        return user

    return dependency


require_user = require_role(UserRole.USER)
require_operator = require_role(UserRole.OPERATOR)
require_admin = require_role(UserRole.ADMIN)
