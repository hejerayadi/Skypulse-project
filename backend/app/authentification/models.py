"""
Every shape the auth stack stores, accepts or returns.

A SkyPulse user is stitched together from two places, and the models mirror
that split exactly:

  * auth.users        — Supabase Auth owns identity: email, password, whether
                        the address was confirmed, when they last signed in.
                        The backend never sees the password.
  * public.profiles   — this project owns the rest: role, status, contact
                        details and console preferences. See schema.sql.

  UserAccount   the two joined together, as the backend works with them.
  UserPublic    the same thing minus raw Supabase metadata — what the API
                returns and the dashboard renders.
  *Request      what a caller is allowed to send. Keeping these separate is
                what stops a client from posting {"role": "admin"} to
                /api/auth/register and being believed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, EmailStr, Field, field_validator

from app.authentification.config import PASSWORD_MIN_LENGTH


# ==========================================================
# Helpers
# ==========================================================

def _ensure_utc(value):
    """
    Postgres hands timestamptz back as an ISO string that pydantic parses fine,
    but a plain `timestamp` column comes back without an offset — and a naive
    datetime blows up the moment it meets datetime.now(timezone.utc).
    """

    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_ensure_utc)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================================
# Enums
# ==========================================================

class UserRole(str, Enum):
    """
    What a signed-in user may do. These are the exact strings allowed by the
    profiles_role_check constraint in schema.sql, and 'user' is the column
    default — so a row created by the trigger is always valid here.

    Ordered least to most privileged; see dependencies.require_role.
    """

    USER = "user"          # read the dashboard
    OPERATOR = "operator"  # read the dashboard and drive the copilot
    ADMIN = "admin"        # everything, plus user administration


ROLE_RANK: dict[UserRole, int] = {
    UserRole.USER: 0,
    UserRole.OPERATOR: 1,
    UserRole.ADMIN: 2,
}


class AccountStatus(str, Enum):
    """
    Whether the account may be used, independent of its role. Distinct from
    Supabase's own email-confirmation state, which is `email_verified` below.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"  # temporarily blocked by an admin
    DISABLED = "disabled"    # deactivated for good


# ==========================================================
# Profile sub-documents
# ==========================================================

class UserProfile(BaseModel):
    """Who the operator is. Optional and user-editable — profiles columns."""

    full_name: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    avatar_url: str | None = Field(default=None, max_length=500)
    country: str | None = Field(default=None, max_length=80)
    timezone: str = "UTC"
    locale: str = "en"


class UserPreferences(BaseModel):
    """Console settings that follow the account rather than the browser."""

    theme: Literal["light", "dark", "system"] = "system"
    default_view: str = "/dashboard"
    email_alerts: bool = True
    alert_on_node_offline: bool = True
    alert_on_abnormality: bool = True
    # Node ids this operator watches; empty means the whole fleet.
    watched_nodes: list[str] = Field(default_factory=list)


# ==========================================================
# The user
# ==========================================================

class UserPublic(BaseModel):
    """
    A user as the outside world may see it. Returned by /api/auth/me, /login,
    /register and /users.
    """

    id: str
    email: EmailStr
    # Supabase's email_confirmed_at, flattened to a boolean.
    email_verified: bool
    role: UserRole
    status: AccountStatus
    profile: UserProfile
    preferences: UserPreferences
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    last_sign_in_at: UtcDatetime | None = None
    # "email", "google", ... — how this account authenticates.
    provider: str | None = None


class UserAccount(BaseModel):
    """
    The backend's working view: the profiles row joined to what Supabase knows
    about the identity.

    `metadata` holds Supabase's raw app_metadata/user_metadata. It stays out of
    UserPublic because it can carry provider internals that no client needs.
    """

    id: str
    email: EmailStr
    email_verified: bool = False
    role: UserRole = UserRole.USER
    status: AccountStatus = AccountStatus.ACTIVE
    profile: UserProfile = Field(default_factory=UserProfile)
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    last_sign_in_at: UtcDatetime | None = None
    provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_public(self) -> UserPublic:
        return UserPublic(**self.model_dump(exclude={"metadata"}))

    @property
    def can_sign_in(self) -> bool:
        return self.status is AccountStatus.ACTIVE

    def has_at_least(self, role: UserRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


# ==========================================================
# Tokens
# ==========================================================

class SupabaseClaims(BaseModel):
    """
    The verified body of a Supabase access token.

    Note what is *not* trusted here: `role` in a Supabase JWT is the Postgres
    role ("authenticated"), not the SkyPulse role. The application role is read
    from the profiles table on every request, so a role change takes effect at
    once instead of waiting for the token to expire.
    """

    sub: str                  # the auth.users id
    email: EmailStr | None = None
    aud: str
    exp: UtcDatetime
    iat: UtcDatetime
    iss: str
    # Present on tokens from projects with session tracking enabled.
    session_id: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)
    user_metadata: dict[str, Any] = Field(default_factory=dict)


class TokenPair(BaseModel):
    """The Supabase session, relayed verbatim."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Seconds until access_token expires — what a non-browser client schedules
    # its refresh against.
    expires_in: int
    expires_at: UtcDatetime | None = None


class AuthSession(BaseModel):
    """
    What /register, /login and /refresh return: who you are plus the tokens.

    Browsers can ignore `tokens` entirely — the same values are set as httpOnly
    cookies. They are in the body for clients that have no cookie jar (Node-RED,
    scripts, a mobile app).
    """

    user: UserPublic
    tokens: TokenPair | None = None
    # Set when Supabase accepted the sign-up but is waiting on the confirmation
    # email, so no session exists yet and `tokens` is null.
    email_confirmation_required: bool = False


# ==========================================================
# Requests
# ==========================================================

def _check_password_length(value: str) -> str:
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        # Supabase enforces its own policy too; this only turns the common case
        # into a clear message instead of a relayed Supabase error.
        return _check_password_length(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Body form of /refresh, for clients that cannot send the cookie."""

    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        return _check_password_length(value)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class UpdateProfileRequest(BaseModel):
    """
    A user editing their own account. Note what is absent: role, status and
    email. Those are set by the server or by an admin, never by the account
    holder — and the RLS policy in schema.sql refuses them a second time at the
    database, in case anything ever reaches Postgres with a raw user token.
    """

    profile: UserProfile | None = None
    preferences: UserPreferences | None = None


class AdminUpdateUserRequest(BaseModel):
    """The admin-only fields, kept in their own model for the same reason."""

    role: UserRole | None = None
    status: AccountStatus | None = None


class UserListResponse(BaseModel):
    users: list[UserPublic]
    total: int


class MessageResponse(BaseModel):
    message: str
