"""
The auth decisions — everything the frontend used to do.

Supabase Auth now owns identity: it hashes the passwords, mints the tokens,
rotates the refresh tokens and sends the confirmation emails. What this layer
owns is what Supabase has no opinion about — what a SkyPulse role means, whether
a suspended account may sign in, who is allowed to change somebody's role, and
the fact that none of it is decided in the browser.

router.py only turns the results into HTTP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.authentification import config, gotrue, repository, security
from app.authentification.gotrue import SupabaseAuthError, SupabaseUnavailable
from app.authentification.models import (
    AccountStatus,
    AdminUpdateUserRequest,
    AuthSession,
    RegisterRequest,
    TokenPair,
    UpdateProfileRequest,
    UserAccount,
    UserRole,
)
from app.authentification.repository import ProfileNotFound


class AuthError(Exception):
    """Base for everything router.py maps onto a 4xx."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidCredentials(AuthError):
    status_code = 401


class AccountNotUsable(AuthError):
    """Valid credentials, but the profile is suspended or disabled."""

    status_code = 403


class PermissionDenied(AuthError):
    status_code = 403


class UserNotFound(AuthError):
    status_code = 404


# ==========================================================
# Startup
# ==========================================================

def start() -> None:
    """
    Report on the Supabase link at boot, rather than letting the first sign-in
    be the thing that discovers a missing key or an unmigrated table.
    """

    for problem in config.describe_configuration():
        print("Auth warning:", problem)

    if not config.is_configured():
        print("Supabase is not configured — /api/auth/* will return 503.")
        return

    print(f"Auth: using Supabase project {config.SUPABASE_URL}")

    try:
        security.reset_key_cache()
        # Fetching the key set now confirms the URL is right and warms the cache,
        # so the first sign-in doesn't pay for it.
        security._client().get_signing_keys()
        print("Auth: Supabase JWKS reachable, tokens will be verified locally.")
    except Exception as e:
        print(
            "Auth: could not fetch the Supabase JWKS "
            f"({e}). Check SUPABASE_URL, or set SUPABASE_JWT_SECRET if this "
            "project still uses the legacy shared secret."
        )

    if not config.SUPABASE_SECRET_KEY:
        return

    try:
        missing = repository.check_schema()
    except Exception as e:
        print("Auth: could not inspect the profiles table:", e)
        return

    if missing:
        print(
            f"Auth: public.profiles is missing {', '.join(missing)}. Sign-in "
            "still works and those fields fall back to their defaults; run "
            "backend/app/authentification/schema.sql in the Supabase SQL editor "
            "to store them."
        )
    else:
        print("Auth: public.profiles schema OK.")

    if config.AUTOCONFIRM_SIGNUP:
        print(
            "Auth: AUTH_AUTOCONFIRM_SIGNUP is on — new accounts are created "
            "already confirmed and no address is ever verified. Turn it off "
            "once real SMTP is configured."
        )


# ==========================================================
# Reading a session
# ==========================================================

def _tokens_from(payload: dict) -> TokenPair | None:
    """Turn a GoTrue token response into a TokenPair, or None when it carried no session."""

    if not payload or not payload.get("access_token"):
        return None

    return TokenPair(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        token_type=payload.get("token_type", "bearer"),
        expires_in=int(payload.get("expires_in", 3600)),
        # Supabase sends expires_at as a unix timestamp, not an ISO string.
        expires_at=(
            datetime.fromtimestamp(payload["expires_at"], tz=timezone.utc)
            if payload.get("expires_at")
            else None
        ),
    )


def _load_account(user_id: str, auth_user: dict | None) -> UserAccount:
    """
    Join the Supabase identity to its profiles row.

    A missing row is repaired rather than raised: the trigger in schema.sql
    normally creates it, but an account made before the trigger existed would
    otherwise be able to sign in and never load.
    """

    row = repository.find_profile(user_id)

    if row is None:
        email = (auth_user or {}).get("email") or ""
        metadata = (auth_user or {}).get("user_metadata") or {}
        row = repository.create_profile(
            user_id,
            email=email,
            role=UserRole.USER,
            full_name=metadata.get("full_name"),
        )

    return repository.row_to_account(row, auth_user)


def _session_from(payload: dict) -> AuthSession:
    """Build the response for /register, /login and /refresh."""

    auth_user = payload.get("user") or {}
    user_id = auth_user.get("id")

    if not user_id:
        raise InvalidCredentials("Supabase returned no user for this session")

    account = _load_account(user_id, auth_user)

    # Checked after Supabase accepted the credentials, so the answer doesn't
    # reveal an account's status to someone who doesn't know its password.
    if not account.can_sign_in:
        raise AccountNotUsable(
            f"This account is {account.status.value} and cannot sign in."
        )

    return AuthSession(user=account.to_public(), tokens=_tokens_from(payload))


# ==========================================================
# Registration and login
# ==========================================================

def register(request: RegisterRequest) -> AuthSession:
    # Read back by the handle_new_user trigger, which writes full_name onto the
    # profiles row it creates.
    metadata = {
        "full_name": request.full_name or "",
        "organization": request.organization or "",
    }

    if config.AUTOCONFIRM_SIGNUP and config.SUPABASE_SECRET_KEY:
        payload = _register_preconfirmed(request, metadata)
    else:
        payload = gotrue.sign_up(
            email=request.email,
            password=request.password,
            metadata=metadata,
        )

    auth_user = payload.get("user") or payload
    user_id = auth_user.get("id")

    if not user_id:
        raise AuthError("Supabase did not create the account")

    account = _load_account(user_id, auth_user)

    if request.organization:
        account = _apply_signup_organization(account, request.organization, auth_user)

    tokens = _tokens_from(payload)

    return AuthSession(
        user=account.to_public(),
        tokens=tokens,
        # With "Confirm email" on, Supabase returns the user but no session: the
        # account is real, it just cannot sign in until the link is clicked.
        email_confirmation_required=tokens is None,
    )


def _register_preconfirmed(request: RegisterRequest, metadata: dict) -> dict:
    """
    Create the account through the admin API and immediately sign in.

    The admin endpoint mails nothing, so this path is unaffected by the quota on
    Supabase's built-in sender — but it also returns no session, hence the
    sign-in that follows.
    """

    gotrue.admin_create_user(
        email=str(request.email),
        password=request.password,
        metadata=metadata,
        email_confirm=True,
    )

    return gotrue.sign_in(email=str(request.email), password=request.password)


def _apply_signup_organization(
    account: UserAccount,
    organization: str,
    auth_user: dict | None = None,
) -> UserAccount:
    """
    The trigger only copies full_name onto the new row, so organization has to
    be written here — after the row exists.
    """

    try:
        row = repository.update_profile(account.id, {"organization": organization})
        return repository.row_to_account(row, auth_user)
    except (ProfileNotFound, SupabaseAuthError):
        # Cosmetic; never worth failing a successful registration over.
        return account


def login(email: str, password: str) -> AuthSession:
    payload = gotrue.sign_in(email=email, password=password)
    return _session_from(payload)


def refresh_session(refresh_token: str) -> AuthSession:
    """
    Trade a refresh token for a fresh pair.

    Supabase rotates the refresh token itself, so each one works once; this only
    relays the exchange and re-checks the profile, because suspending an account
    has to take effect within one access-token lifetime rather than one refresh
    lifetime.
    """

    payload = gotrue.refresh(refresh_token)
    return _session_from(payload)


def logout(access_token: str | None, everywhere: bool = False) -> None:
    """Revoke the session at Supabase. A missing or already-dead token is a no-op."""

    if not access_token:
        return

    try:
        gotrue.sign_out(access_token, scope="global" if everywhere else "local")
    except SupabaseAuthError:
        # An expired token is already logged out as far as the user is concerned.
        return


# ==========================================================
# Authorising a request
# ==========================================================

def resolve_access_token(token: str) -> UserAccount:
    """
    Turn a cookie/bearer access token into the live account.

    The signature is checked locally against the project's JWKS, then the
    profile is re-read from Postgres — so a role change or a suspension applies
    on the next request instead of waiting for the token to expire.
    """

    try:
        claims = security.verify_access_token(token)
    except security.InvalidToken as e:
        raise InvalidCredentials(str(e))

    try:
        account = _load_account(claims.sub, {"email": claims.email})
    except ProfileNotFound:
        raise InvalidCredentials("The account for this token no longer exists")

    if not account.can_sign_in:
        raise AccountNotUsable(f"This account is {account.status.value}.")

    return account


# ==========================================================
# Account self-service
# ==========================================================

def update_own_account(user: UserAccount, request: UpdateProfileRequest) -> UserAccount:
    changes = repository.profile_to_row(request.profile, request.preferences)

    if not changes:
        return user

    row = repository.update_profile(user.id, changes)
    return repository.row_to_account(row)


def change_password(user: UserAccount, access_token: str, current: str, new: str) -> None:
    """
    Supabase's update-user endpoint does not ask for the current password, so it
    is verified here first — otherwise anyone holding a stolen access token could
    lock the real owner out of their account.
    """

    if current == new:
        raise AuthError("The new password must be different from the current one")

    try:
        gotrue.sign_in(email=str(user.email), password=current)
    except SupabaseAuthError:
        raise InvalidCredentials("Current password is incorrect")

    gotrue.update_user(access_token, {"password": new})

    # A password change is how you respond to a suspected compromise, so every
    # other session has to go with it.
    try:
        gotrue.admin_sign_out_user(user.id)
    except SupabaseAuthError:
        pass


def send_password_reset(email: str) -> None:
    """
    Always reported as sent. Whether the address exists is not something an
    unauthenticated caller gets to learn from the response.
    """

    try:
        gotrue.send_password_reset(email)
    except SupabaseAuthError:
        pass


# ==========================================================
# Administration
# ==========================================================

def list_users(limit: int = 100, offset: int = 0):
    rows, total = repository.list_profiles(limit=limit, offset=offset)

    # Confirmation state and last sign-in live in auth.users, not profiles, so
    # they are fetched once for the page rather than per row.
    auth_users: dict[str, dict] = {}
    try:
        listing = gotrue.admin_list_users(per_page=max(limit, 100))
        for entry in listing.get("users", []) if isinstance(listing, dict) else []:
            auth_users[entry.get("id")] = entry
    except (SupabaseAuthError, SupabaseUnavailable):
        # The profiles half is still worth returning without it.
        pass

    return [
        repository.row_to_account(row, auth_users.get(str(row["id"]))).to_public()
        for row in rows
    ], total


def admin_update_user(
    actor: UserAccount,
    user_id: str,
    request: AdminUpdateUserRequest,
) -> UserAccount:
    row = repository.find_profile(user_id)
    if row is None:
        raise UserNotFound("No such user")

    target = repository.row_to_account(row)

    # An admin demoting or disabling themselves can leave the project with no
    # administrator at all, so both are refused outright.
    if actor.id == target.id:
        if request.role is not None and request.role is not UserRole.ADMIN:
            raise PermissionDenied("You cannot remove your own admin role")
        if request.status is not None and request.status is not AccountStatus.ACTIVE:
            raise PermissionDenied("You cannot deactivate your own account")

    changes: dict[str, Any] = {}
    if request.role is not None:
        changes["role"] = request.role.value
    if request.status is not None:
        changes["status"] = request.status.value

    if not changes:
        return target

    updated = repository.row_to_account(repository.update_profile(user_id, changes))

    # Anything that narrows what the account may do takes effect now: the old
    # access token stays cryptographically valid until it expires, so the
    # sessions behind it have to be revoked at Supabase.
    role_changed = request.role is not None and request.role is not target.role
    if not updated.can_sign_in or role_changed:
        try:
            gotrue.admin_sign_out_user(user_id)
        except (SupabaseAuthError, SupabaseUnavailable):
            pass

    return updated


def admin_delete_user(actor: UserAccount, user_id: str) -> None:
    if actor.id == user_id:
        raise PermissionDenied("You cannot delete your own account")

    if repository.find_profile(user_id) is None:
        raise UserNotFound("No such user")

    # Deleting the auth.users row cascades to profiles, thanks to the
    # `on delete cascade` on the foreign key.
    gotrue.admin_delete_user(user_id)
