"""
SkyPulse authentication, backed by your own Supabase project.

Identity lives in Supabase Auth (auth.users) — it hashes the passwords, mints
and rotates the tokens, and sends the confirmation and reset emails. This
package is what sits in front of it: it holds the session in httpOnly cookies
the browser cannot read, joins each user to their public.profiles row for role
and preferences, and enforces the rules Supabase has no opinion about.

Nothing outside this package decides whether a caller is signed in. The frontend
only renders the form and displays whatever /api/auth/me returns.

    schema.sql       run this in the Supabase SQL editor first
    config.py        settings and their defaults
    models.py        the user model, tokens, request/response shapes
    security.py      verifies Supabase access tokens against the project JWKS
    gotrue.py        client for Supabase Auth (/auth/v1)
    repository.py    client for public.profiles (/rest/v1)
    service.py       the rules: roles, account status, who may change what
    dependencies.py  Depends(...) guards other routers put on their endpoints
    router.py        /api/auth/*

Guarding an endpoint elsewhere in the backend:

    from app.authentification import get_current_user, require_admin
"""

from app.authentification.dependencies import (
    get_current_user,
    get_optional_user,
    require_admin,
    require_operator,
    require_role,
    require_user,
)
from app.authentification.models import (
    AccountStatus,
    UserAccount,
    UserPublic,
    UserRole,
)
from app.authentification.router import router
from app.authentification.service import start

__all__ = [
    "AccountStatus",
    "UserAccount",
    "UserPublic",
    "UserRole",
    "get_current_user",
    "get_optional_user",
    "require_admin",
    "require_operator",
    "require_role",
    "require_user",
    "router",
    "start",
]
