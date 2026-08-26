"""
Every knob the auth stack reads, resolved once at import.

Identity lives in your Supabase project, so there is no password policy or JWT
secret to configure here — Supabase Auth owns both. What is left is where the
project is, which keys to present, and how the session cookies are written.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# ==========================================================
# Supabase project
# ==========================================================

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")

# The publishable / anon key. Safe to expose — it only permits the public auth
# endpoints (sign up, sign in, refresh) and whatever RLS allows. This is the key
# the backend presents when acting *as the user*.
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""

# The secret / service_role key. Bypasses Row Level Security and unlocks the
# admin endpoints, so it must never reach the browser — it is read here, in the
# backend, and nowhere else in this repository.
SUPABASE_SECRET_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

AUTH_URL = f"{SUPABASE_URL}/auth/v1"
REST_URL = f"{SUPABASE_URL}/rest/v1"
JWKS_URL = f"{AUTH_URL}/.well-known/jwks.json"

PROFILES_TABLE = os.getenv("SUPABASE_PROFILES_TABLE", "profiles")

# Supabase stamps every user token with aud "authenticated" and iss <auth url>.
# Verifying both is what stops a token minted by some other project from being
# accepted by this one.
JWT_AUDIENCE = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")
JWT_ISSUER = AUTH_URL

# Only needed by projects still on the legacy shared HS256 secret. Projects
# created with asymmetric signing keys — including this one — verify against the
# public JWKS instead and can leave it unset.
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET") or ""

# Seconds before the cached JWKS is re-fetched. Supabase rotates signing keys,
# and a stale cache would reject every token minted with the new one.
JWKS_CACHE_SECONDS = int(os.getenv("SUPABASE_JWKS_CACHE_SECONDS", "600"))

HTTP_TIMEOUT_SECONDS = float(os.getenv("SUPABASE_HTTP_TIMEOUT", "15"))


# ==========================================================
# Registration
# ==========================================================

# Where Supabase sends the user after they click the link in a confirmation or
# password-reset email. Must be listed under Authentication -> URL Configuration
# -> Redirect URLs in the dashboard, or Supabase ignores it.
EMAIL_REDIRECT_URL = os.getenv("AUTH_EMAIL_REDIRECT_URL", "http://localhost:5173/dashboard")
PASSWORD_RESET_REDIRECT_URL = os.getenv(
    "AUTH_PASSWORD_RESET_REDIRECT_URL", "http://localhost:5173/auth"
)

# Mirrors the minimum length configured in the dashboard, so the API can reject
# a short password with a clear message instead of relaying Supabase's.
PASSWORD_MIN_LENGTH = int(os.getenv("AUTH_PASSWORD_MIN_LENGTH", "8"))

# Create accounts already confirmed, through the admin API, instead of going
# through Supabase's public sign-up.
#
# Supabase's built-in mail sender allows only a couple of messages an hour — it
# exists for testing, not for use — so a project with "Confirm email" on hits
# "email rate limit exceeded" after two or three sign-ups and nobody can
# register at all. The admin path sends no email, so registration keeps working
# regardless of that setting or that quota.
#
# The cost is that nobody proves they own the address. Right for a ground
# station whose operators are known; wrong for anything open to the public, so
# it stays off unless asked for, and needs the secret key.
AUTOCONFIRM_SIGNUP = os.getenv("AUTH_AUTOCONFIRM_SIGNUP", "false").lower() == "true"


# ==========================================================
# Cookies
# ==========================================================

# The browser never reads these — they are httpOnly, so the Supabase tokens stay
# out of reach of any script on the page. That is the main thing this backend
# adds over calling Supabase straight from the frontend.
ACCESS_COOKIE_NAME = os.getenv("AUTH_ACCESS_COOKIE", "skypulse_access")
REFRESH_COOKIE_NAME = os.getenv("AUTH_REFRESH_COOKIE", "skypulse_refresh")

# Secure cookies are dropped by browsers over plain http, so this has to stay
# off for http://localhost and on everywhere else.
COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN") or None

# The refresh cookie is only ever read by /api/auth/refresh and /api/auth/logout,
# so scoping it there keeps it off every other request.
REFRESH_COOKIE_PATH = "/api/auth"

# Supabase access tokens last an hour by default (Authentication -> Sessions).
# The cookie is given the same life so the browser drops it when it dies.
ACCESS_COOKIE_TTL_SECONDS = int(os.getenv("AUTH_ACCESS_COOKIE_TTL", str(60 * 60)))
REFRESH_COOKIE_TTL_SECONDS = int(os.getenv("AUTH_REFRESH_COOKIE_TTL", str(30 * 24 * 60 * 60)))


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)


def describe_configuration() -> list[str]:
    """Problems worth printing at startup, empty when the setup is sound."""

    problems: list[str] = []

    if not SUPABASE_URL:
        problems.append("SUPABASE_URL is not set — /api/auth/* will return 503.")

    if not SUPABASE_PUBLISHABLE_KEY:
        problems.append(
            "SUPABASE_PUBLISHABLE_KEY is not set — sign up, sign in and refresh "
            "cannot reach Supabase."
        )

    if not SUPABASE_SECRET_KEY:
        problems.append(
            "SUPABASE_SECRET_KEY is not set — profiles are behind Row Level "
            "Security, so roles and admin endpoints will not work."
        )

    if not COOKIE_SECURE:
        problems.append(
            "AUTH_COOKIE_SECURE is false — session cookies will travel over plain "
            "http. Set it to true anywhere the app is served over https."
        )

    return problems
