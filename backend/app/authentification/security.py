"""
Verifying the access tokens Supabase issues.

There is no password hashing here any more — Supabase Auth holds the passwords
and this backend never sees one. What is left is proving that a token really
came from your project and has not expired.

Your project signs with ES256 and publishes the public half at
/auth/v1/.well-known/jwks.json, so verification is a local signature check
against a cached key: no round trip to Supabase on every request. Projects
still on the legacy shared HS256 secret are handled too, via SUPABASE_JWT_SECRET.
"""

from __future__ import annotations

import threading
import time

import jwt
from jwt import PyJWKClient

from app.authentification import config
from app.authentification.models import SupabaseClaims


class InvalidToken(Exception):
    """Raised for anything that isn't a valid, unexpired token from this project."""


# PyJWKClient does its own caching, but it is built lazily and guarded here so
# two concurrent requests on a cold start don't each fetch the key set.
_jwk_client: PyJWKClient | None = None
_jwk_client_built_at: float = 0.0
_lock = threading.Lock()


def _client() -> PyJWKClient:
    global _jwk_client, _jwk_client_built_at

    with _lock:
        expired = time.monotonic() - _jwk_client_built_at > config.JWKS_CACHE_SECONDS

        # Rebuilding on expiry is what picks up a rotated signing key. Without
        # it, a rotation in the Supabase dashboard would reject every token.
        if _jwk_client is None or expired:
            _jwk_client = PyJWKClient(
                config.JWKS_URL,
                cache_keys=True,
                timeout=int(config.HTTP_TIMEOUT_SECONDS),
            )
            _jwk_client_built_at = time.monotonic()

        return _jwk_client


def _signing_key(token: str, algorithm: str):
    if algorithm.startswith("HS"):
        if not config.JWT_SECRET:
            raise InvalidToken(
                "This token is signed with the legacy shared secret, but "
                "SUPABASE_JWT_SECRET is not set."
            )
        return config.JWT_SECRET

    try:
        return _client().get_signing_key_from_jwt(token).key
    except Exception as e:
        raise InvalidToken(f"Could not resolve the signing key: {e}") from e


def verify_access_token(token: str) -> SupabaseClaims:
    """Verify a Supabase access token and return its claims, or raise InvalidToken."""

    if not token:
        raise InvalidToken("No token supplied")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise InvalidToken("Token is not a readable JWT") from e

    algorithm = header.get("alg")
    if not algorithm or algorithm == "none":
        raise InvalidToken("Token declares no signing algorithm")

    key = _signing_key(token, algorithm)

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            # Both are checked because a token minted by a *different* Supabase
            # project is otherwise perfectly valid — same algorithm, same shape.
            audience=config.JWT_AUDIENCE,
            issuer=config.JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError as e:
        raise InvalidToken("Session has expired") from e
    except jwt.InvalidAudienceError as e:
        raise InvalidToken("Token was not issued for this application") from e
    except jwt.InvalidIssuerError as e:
        raise InvalidToken("Token was issued by a different Supabase project") from e
    except jwt.InvalidTokenError as e:
        raise InvalidToken("Token is not valid") from e

    try:
        return SupabaseClaims(**payload)
    except Exception as e:
        raise InvalidToken("Token payload is not a Supabase user token") from e


def reset_key_cache() -> None:
    """Drop the cached JWKS. Used by the startup check and by tests."""

    global _jwk_client, _jwk_client_built_at

    with _lock:
        _jwk_client = None
        _jwk_client_built_at = 0.0
