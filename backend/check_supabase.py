"""
Confirm the backend can actually talk to your Supabase project.

Run it after filling in backend/.env and running schema.sql:

    cd backend
    venv\\Scripts\\python.exe check_supabase.py

It checks the URL, both keys, the JWKS, and the profiles table, then — unless
you pass --no-signup — registers a throwaway account to exercise the full
sign-up / sign-in / refresh / logout path and deletes it again afterwards.
"""

from __future__ import annotations

import sys
import uuid

from app.authentification import config, gotrue, repository, security

FAILED = False


def report(label: str, ok: bool, detail: str = "") -> bool:
    global FAILED
    # Plain ASCII separator: the Windows console default codepage mangles an
    # em dash into a replacement character.
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        FAILED = True
    return ok


def main() -> int:
    run_signup = "--no-signup" not in sys.argv

    print("\n1. Configuration")
    report("SUPABASE_URL", bool(config.SUPABASE_URL), config.SUPABASE_URL or "not set")
    report("SUPABASE_PUBLISHABLE_KEY", bool(config.SUPABASE_PUBLISHABLE_KEY))
    report("SUPABASE_SECRET_KEY", bool(config.SUPABASE_SECRET_KEY))

    if not config.is_configured():
        print("\nFill in backend/.env before running the rest.")
        return 1

    print("\n2. Token signing keys")
    try:
        keys = security._client().get_signing_keys()
        report(
            "JWKS reachable",
            bool(keys),
            f"{len(keys)} key(s): {', '.join(k._jwk_data.get('alg', '?') for k in keys)}",
        )
    except Exception as e:
        report("JWKS reachable", False, str(e))
        if not config.JWT_SECRET:
            print("     Set SUPABASE_JWT_SECRET if this project uses the legacy shared secret.")

    print("\n3. The profiles table")
    if not config.SUPABASE_SECRET_KEY:
        report("profiles readable", False, "needs SUPABASE_SECRET_KEY")
    else:
        try:
            missing = repository.check_schema()
            if missing:
                report("profiles schema", False, f"missing columns: {', '.join(missing)}")
                print("     Run backend/app/authentification/schema.sql in the SQL editor.")
            else:
                report("profiles schema", True, "all expected columns present")

            total = repository.count_profiles()
            report("profiles readable", True, f"{total} account(s)")
        except Exception as e:
            report("profiles readable", False, str(e))

    if not run_signup:
        print()
        return 1 if FAILED else 0

    print("\n4. Live sign-up / sign-in / refresh / logout")
    email = f"skypulse-check-{uuid.uuid4().hex[:10]}@example.com"
    password = f"Check-{uuid.uuid4().hex[:12]}"
    user_id = None

    try:
        signup = gotrue.sign_up(email, password, {"full_name": "SkyPulse Self Check"})
        user = signup.get("user") or signup
        user_id = user.get("id")
        report("sign up", bool(user_id), email)

        if not signup.get("access_token"):
            print("     Email confirmation is on, so there is no session to test.")
            print("     That is a valid setup — the frontend handles it.")
        else:
            claims = security.verify_access_token(signup["access_token"])
            report("token verifies locally", claims.sub == user_id)

            session = gotrue.sign_in(email, password)
            report("sign in", bool(session.get("access_token")))

            refreshed = gotrue.refresh(session["refresh_token"])
            report("refresh", bool(refreshed.get("access_token")))

            gotrue.sign_out(refreshed["access_token"], scope="global")
            report("sign out", True)

        row = repository.find_profile(user_id)
        report(
            "trigger created the profiles row",
            row is not None,
            f"role={row.get('role')}" if row else "no row — is on_auth_user_created installed?",
        )

    except Exception as e:
        report("live auth flow", False, str(e))

    finally:
        if user_id:
            try:
                gotrue.admin_delete_user(user_id)
                print(f"  OK    cleaned up the test account")
            except Exception as e:
                print(f"  NOTE  could not delete the test account {email}: {e}")

    print()
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
