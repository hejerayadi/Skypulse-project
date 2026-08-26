"""
The client for Supabase Auth (GoTrue), your project's /auth/v1 API.

Called straight over HTTP rather than through supabase-py: this backend needs
six endpoints, all of them plain JSON, and talking to them directly keeps the
dependency tree small and the request/response shapes visible.

Two keys, used for two different things:

  * the publishable key acts *as the user* — sign up, sign in, refresh, logout.
  * the secret key acts *as the project* — listing and administering accounts.

Nothing here interprets a session; it relays what Supabase said. The rules live
in service.py.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.authentification import config


class SupabaseUnavailable(Exception):
    """Your project could not be reached — the API turns this into a 503."""


class SupabaseAuthError(Exception):
    """Supabase refused the request. Carries its status so the router can reuse it."""

    def __init__(self, message: str, status_code: int = 400, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _headers(key: str, access_token: str | None = None) -> dict[str, str]:
    return {
        "apikey": key,
        # Supabase wants the caller's token here when acting for a user, and the
        # key itself otherwise.
        "Authorization": f"Bearer {access_token or key}",
        "Content-Type": "application/json",
    }


def _extract_error(response: httpx.Response) -> SupabaseAuthError:
    """
    GoTrue has used three error shapes over its life — {error, error_description},
    {code, msg} and {error_code, msg} — and a project can return any of them
    depending on the endpoint. All three end up in a toast, so all three have to
    resolve to one sentence.
    """

    message = None
    code = None

    try:
        body = response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        code = body.get("error_code") or body.get("code")
        message = (
            body.get("error_description")
            or body.get("msg")
            or body.get("message")
            or (body.get("error") if isinstance(body.get("error"), str) else None)
        )

    if not message:
        message = f"Supabase rejected the request ({response.status_code})"

    # GoTrue answers 400 for bad credentials; surfacing that as 401 is what lets
    # the frontend tell "wrong password" from "malformed request".
    status = response.status_code
    if status == 400 and code in {"invalid_credentials", "invalid_grant"}:
        status = 401

    return SupabaseAuthError(message, status_code=status, code=str(code) if code else None)


def _request(
    method: str,
    path: str,
    *,
    key: str,
    access_token: str | None = None,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    if not config.is_configured():
        raise SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_PUBLISHABLE_KEY in backend/.env."
        )

    url = f"{config.AUTH_URL}{path}"

    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(key, access_token),
            json=json,
            params=params,
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise SupabaseUnavailable(f"Could not reach Supabase Auth: {e}") from e

    if response.status_code >= 400:
        raise _extract_error(response)

    if not response.content:
        return None

    try:
        return response.json()
    except Exception:
        return None


def _as_user(path: str, method: str = "POST", **kwargs) -> Any:
    return _request(method, path, key=config.SUPABASE_PUBLISHABLE_KEY, **kwargs)


def _as_project(path: str, method: str = "GET", **kwargs) -> Any:
    if not config.SUPABASE_SECRET_KEY:
        raise SupabaseAuthError(
            "This action needs the Supabase secret key. Set SUPABASE_SECRET_KEY "
            "in backend/.env.",
            status_code=503,
        )
    return _request(method, path, key=config.SUPABASE_SECRET_KEY, **kwargs)


# ==========================================================
# Public auth endpoints
# ==========================================================

def sign_up(email: str, password: str, metadata: dict[str, Any] | None = None) -> dict:
    """
    Create the account. The trigger in schema.sql turns the resulting auth.users
    row into a profiles row, which is where full_name below ends up.

    Returns a session when email confirmation is off, and a bare user when it is
    on — service.py distinguishes the two.
    """

    return _as_user(
        "/signup",
        json={
            "email": email,
            "password": password,
            # Supabase stores this as raw_user_meta_data.
            "data": metadata or {},
            "options": {"email_redirect_to": config.EMAIL_REDIRECT_URL},
        },
    )


def sign_in(email: str, password: str) -> dict:
    return _as_user(
        "/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
    )


def refresh(refresh_token: str) -> dict:
    return _as_user(
        "/token",
        params={"grant_type": "refresh_token"},
        json={"refresh_token": refresh_token},
    )


def sign_out(access_token: str, scope: str = "local") -> None:
    """
    scope="local" ends this session, "global" ends every session for the account.
    Supabase answers 204 with no body.
    """

    _as_user("/logout", params={"scope": scope}, access_token=access_token)


def get_user(access_token: str) -> dict:
    """Ask Supabase who a token belongs to. Used by the startup self-check."""

    return _as_user("/user", method="GET", access_token=access_token)


def update_user(access_token: str, changes: dict[str, Any]) -> dict:
    """Change the caller's own email, password or metadata."""

    return _as_user("/user", method="PUT", access_token=access_token, json=changes)


def send_password_reset(email: str) -> None:
    _as_user(
        "/recover",
        json={"email": email},
        params={"redirect_to": config.PASSWORD_RESET_REDIRECT_URL},
    )


def resend_confirmation(email: str) -> None:
    _as_user(
        "/resend",
        json={"type": "signup", "email": email},
        params={"redirect_to": config.EMAIL_REDIRECT_URL},
    )


# ==========================================================
# Admin endpoints (secret key)
# ==========================================================

def admin_list_users(page: int = 1, per_page: int = 100) -> dict:
    return _as_project("/admin/users", params={"page": page, "per_page": per_page})


def admin_get_user(user_id: str) -> dict:
    return _as_project(f"/admin/users/{user_id}")


def admin_create_user(
    email: str,
    password: str,
    metadata: dict[str, Any] | None = None,
    email_confirm: bool = True,
) -> dict:
    """
    Create an account directly, already confirmed.

    Unlike /signup this sends no email at all, which is what makes it immune to
    the rate limit on Supabase's built-in sender. It returns the user but no
    session, so the caller signs in afterwards to open one.
    """

    return _as_project(
        "/admin/users",
        method="POST",
        json={
            "email": email,
            "password": password,
            # Marks the address confirmed without ever mailing it.
            "email_confirm": email_confirm,
            "user_metadata": metadata or {},
        },
    )


def admin_confirm_email(user_id: str) -> dict:
    """Mark an existing account's address confirmed, again without emailing it."""

    return _as_project(
        f"/admin/users/{user_id}",
        method="PUT",
        json={"email_confirm": True},
    )


def admin_delete_user(user_id: str) -> None:
    _as_project(f"/admin/users/{user_id}", method="DELETE")


def admin_sign_out_user(user_id: str) -> None:
    """
    End every session an account has.

    Used when an admin suspends or demotes somebody: their existing access token
    stays cryptographically valid until it expires, so revoking it at Supabase
    is what makes the change take effect now rather than in an hour.
    """

    try:
        _as_project(f"/admin/users/{user_id}/sessions", method="DELETE")
    except SupabaseAuthError as e:
        # Older projects don't expose this route. Losing it only means the change
        # lands when the token expires, which must not fail the whole request.
        if e.status_code not in (404, 405):
            raise
