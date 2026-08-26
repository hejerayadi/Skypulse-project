"""
The public.profiles table, reached through Supabase's REST API (PostgREST).

Everything here uses the secret key, which bypasses Row Level Security. That is
deliberate and safe only because it happens inside the backend: this module is
the one place allowed to read another user's role, and service.py decides
whether the caller may ask.

The RLS policies in schema.sql still matter — they are what protects the table
from anything that ever reaches Postgres holding a plain user token.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from app.authentification import config
from app.authentification.gotrue import SupabaseAuthError, SupabaseUnavailable
from app.authentification.models import (
    AccountStatus,
    UserAccount,
    UserPreferences,
    UserProfile,
    UserRole,
)

# Columns without which nothing works. Their absence is a real error.
REQUIRED_COLUMNS = ["id", "email", "role"]

# Everything the API would like to use. The ones schema.sql adds are optional at
# runtime: a project that has not run the migration yet still signs people in,
# with those fields falling back to the model defaults, instead of failing every
# request with "column profiles.status does not exist".
PROFILE_COLUMNS = [
    "id",
    "email",
    "full_name",
    "role",
    "status",
    "organization",
    "job_title",
    "phone",
    "avatar_url",
    "country",
    "timezone",
    "locale",
    "theme",
    "default_view",
    "email_alerts",
    "alert_on_node_offline",
    "alert_on_abnormality",
    "watched_nodes",
    "created_at",
    "updated_at",
]


class ProfileNotFound(Exception):
    """No profiles row for an account that exists in Supabase Auth."""


# ==========================================================
# Which columns the table actually has
# ==========================================================

_schema_lock = threading.Lock()
_known_columns: set[str] | None = None


def _fetch_columns() -> set[str]:
    """
    Ask PostgREST what the table looks like.

    Its root endpoint serves an OpenAPI document describing every exposed table,
    so one request answers this — cheaper and more reliable than probing each
    column with a query and reading the errors.
    """

    key = config.SUPABASE_SECRET_KEY

    response = httpx.get(
        f"{config.REST_URL}/",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=config.HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    definition = (response.json().get("definitions") or {}).get(config.PROFILES_TABLE) or {}
    return set((definition.get("properties") or {}).keys())


def known_columns(refresh: bool = False) -> set[str]:
    global _known_columns

    if _known_columns is not None and not refresh:
        return _known_columns

    with _schema_lock:
        if _known_columns is not None and not refresh:
            return _known_columns

        try:
            _known_columns = _fetch_columns()
        except Exception:
            # If the introspection call fails, assume the full schema and let
            # the individual queries report whatever is really wrong.
            _known_columns = set(PROFILE_COLUMNS)

        return _known_columns


def usable_columns() -> list[str]:
    """The intersection of what the code wants and what the table has."""

    available = known_columns()
    return [column for column in PROFILE_COLUMNS if column in available]


def missing_columns() -> list[str]:
    available = known_columns()
    return [column for column in PROFILE_COLUMNS if column not in available]


def _select() -> str:
    return ",".join(usable_columns())


def _writable(changes: dict) -> dict:
    """Drop keys the table has no column for, so a partial schema still saves."""

    available = known_columns()
    return {key: value for key, value in changes.items() if key in available}


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = config.SUPABASE_SECRET_KEY
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **(extra or {}),
    }


def _request(method: str, params: dict[str, Any] | None = None, json: Any = None,
             extra_headers: dict[str, str] | None = None) -> Any:
    if not config.SUPABASE_URL:
        raise SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL in backend/.env."
        )

    if not config.SUPABASE_SECRET_KEY:
        raise SupabaseAuthError(
            "Profiles are behind Row Level Security and need the Supabase secret "
            "key. Set SUPABASE_SECRET_KEY in backend/.env.",
            status_code=503,
        )

    url = f"{config.REST_URL}/{config.PROFILES_TABLE}"

    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(extra_headers),
            params=params,
            json=json,
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise SupabaseUnavailable(f"Could not reach the Supabase database: {e}") from e

    if response.status_code >= 400:
        message = None
        try:
            body = response.json()
            if isinstance(body, dict):
                message = body.get("message") or body.get("hint") or body.get("details")
        except Exception:
            pass

        raise SupabaseAuthError(
            message or f"Supabase rejected the query ({response.status_code})",
            status_code=response.status_code,
        )

    if not response.content:
        return None

    try:
        return response.json()
    except Exception:
        return None


# ==========================================================
# Row <-> model
# ==========================================================

def _coerce_role(value: Any) -> UserRole:
    try:
        return UserRole(value)
    except ValueError:
        # A role the constraint should have blocked. Falling back to the least
        # privileged value fails closed instead of handing out access.
        return UserRole.USER


def _coerce_status(value: Any) -> AccountStatus:
    try:
        return AccountStatus(value)
    except ValueError:
        return AccountStatus.ACTIVE


def row_to_account(row: dict, auth_user: dict | None = None) -> UserAccount:
    """
    Join a profiles row to what Supabase Auth knows about the same account.

    `auth_user` is whatever /auth/v1/user or the admin API returned. It is
    optional because the token itself already carries the id and email, so a
    request can be authorised without a second call to Supabase.
    """

    auth_user = auth_user or {}
    identities = auth_user.get("identities") or []

    return UserAccount(
        id=str(row["id"]),
        email=row.get("email") or auth_user.get("email") or "",
        email_verified=bool(auth_user.get("email_confirmed_at")),
        role=_coerce_role(row.get("role")),
        status=_coerce_status(row.get("status")),
        profile=UserProfile(
            full_name=row.get("full_name") or None,
            organization=row.get("organization"),
            job_title=row.get("job_title"),
            phone=row.get("phone"),
            avatar_url=row.get("avatar_url"),
            country=row.get("country"),
            timezone=row.get("timezone") or "UTC",
            locale=row.get("locale") or "en",
        ),
        preferences=UserPreferences(
            theme=row.get("theme") or "system",
            default_view=row.get("default_view") or "/dashboard",
            email_alerts=bool(row.get("email_alerts", True)),
            alert_on_node_offline=bool(row.get("alert_on_node_offline", True)),
            alert_on_abnormality=bool(row.get("alert_on_abnormality", True)),
            watched_nodes=list(row.get("watched_nodes") or []),
        ),
        created_at=row.get("created_at") or auth_user.get("created_at"),
        updated_at=row.get("updated_at"),
        last_sign_in_at=auth_user.get("last_sign_in_at"),
        provider=(
            auth_user.get("app_metadata", {}).get("provider")
            or (identities[0].get("provider") if identities else None)
        ),
        metadata={
            "app_metadata": auth_user.get("app_metadata", {}),
            "user_metadata": auth_user.get("user_metadata", {}),
        },
    )


def profile_to_row(profile: UserProfile | None, preferences: UserPreferences | None) -> dict:
    """Flatten the nested models back onto the flat profiles columns."""

    row: dict[str, Any] = {}

    if profile is not None:
        row.update(
            {
                "full_name": profile.full_name,
                "organization": profile.organization,
                "job_title": profile.job_title,
                "phone": profile.phone,
                "avatar_url": profile.avatar_url,
                "country": profile.country,
                "timezone": profile.timezone,
                "locale": profile.locale,
            }
        )

    if preferences is not None:
        row.update(
            {
                "theme": preferences.theme,
                "default_view": preferences.default_view,
                "email_alerts": preferences.email_alerts,
                "alert_on_node_offline": preferences.alert_on_node_offline,
                "alert_on_abnormality": preferences.alert_on_abnormality,
                "watched_nodes": preferences.watched_nodes,
            }
        )

    return row


# ==========================================================
# Queries
# ==========================================================

def get_profile(user_id: str) -> dict:
    rows = _request("GET", params={"id": f"eq.{user_id}", "select": _select(), "limit": 1})

    if not rows:
        raise ProfileNotFound(user_id)

    return rows[0]


def find_profile(user_id: str) -> dict | None:
    try:
        return get_profile(user_id)
    except ProfileNotFound:
        return None


def create_profile(user_id: str, email: str, role: UserRole = UserRole.USER, **columns) -> dict:
    """
    Insert a missing profiles row.

    The trigger in schema.sql normally does this, so reaching here means either
    the trigger was never installed or the account predates it. Either way the
    user must not be left able to sign in but unable to load.
    """

    payload = _writable({"id": user_id, "email": email, "role": role.value, **columns})

    rows = _request(
        "POST",
        params={"select": _select()},
        json=payload,
        # merge-duplicates makes this an upsert, so two requests racing on the
        # same missing profile don't turn into a primary-key error.
        extra_headers={"Prefer": "return=representation,resolution=merge-duplicates"},
    )

    return rows[0] if rows else payload


def update_profile(user_id: str, changes: dict) -> dict:
    changes = _writable(changes)

    if not changes:
        return get_profile(user_id)

    rows = _request(
        "PATCH",
        params={"id": f"eq.{user_id}", "select": _select()},
        json=changes,
        extra_headers={"Prefer": "return=representation"},
    )

    if not rows:
        raise ProfileNotFound(user_id)

    return rows[0]


def list_profiles(limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    if not config.SUPABASE_URL:
        raise SupabaseUnavailable("Supabase is not configured.")

    url = f"{config.REST_URL}/{config.PROFILES_TABLE}"

    try:
        response = httpx.get(
            url,
            headers=_headers(
                # count=exact makes PostgREST report the total in Content-Range,
                # so the caller gets a real total rather than the page length.
                {"Prefer": "count=exact", "Range-Unit": "items",
                 "Range": f"{offset}-{offset + limit - 1}"}
            ),
            params={"select": _select(), "order": "created_at.desc"},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise SupabaseUnavailable(f"Could not reach the Supabase database: {e}") from e

    if response.status_code >= 400:
        raise SupabaseAuthError(
            f"Supabase rejected the query ({response.status_code})",
            status_code=response.status_code,
        )

    rows = response.json() or []

    # Content-Range comes back as "0-24/137"; the tail is the total.
    total = len(rows)
    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        tail = content_range.split("/")[-1]
        if tail.isdigit():
            total = int(tail)

    return rows, total


def delete_profile(user_id: str) -> None:
    # Normally redundant: deleting the auth.users row cascades to profiles.
    _request("DELETE", params={"id": f"eq.{user_id}"})


def count_profiles() -> int:
    _, total = list_profiles(limit=1)
    return total


def check_schema() -> list[str]:
    """
    Report the columns schema.sql would add that the table does not have yet.

    Called at startup so a pending migration is named explicitly, rather than
    being discovered later as a silently defaulted field.
    """

    refresh_schema()
    return missing_columns()


def refresh_schema() -> set[str]:
    """Re-introspect the table. Call after running schema.sql, or at startup."""

    return known_columns(refresh=True)
