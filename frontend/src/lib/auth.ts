// The frontend's entire relationship with authentication: call the backend and
// render what comes back.
//
// There is no token here on purpose, and no Supabase client either. The backend
// talks to Supabase Auth and sets the resulting session as httpOnly cookies, so
// nothing in this file — or anywhere else in the bundle — can read them, and
// `credentials: "include"` is all it takes to stay signed in. Who may sign in,
// when a session expires, what a role permits: all of that is decided in
// backend/app/authentification.

import { apiUrl } from "./api";

// ---------------------------------------------------------------------------
// Shapes, mirroring backend/app/authentification/models.py
// ---------------------------------------------------------------------------

// Matches the profiles_role_check / profiles_status_check constraints in
// backend/app/authentification/schema.sql.
export type UserRole = "user" | "operator" | "admin";
export type AccountStatus = "active" | "suspended" | "disabled";

export interface UserProfile {
  full_name: string | null;
  organization: string | null;
  job_title: string | null;
  phone: string | null;
  avatar_url: string | null;
  country: string | null;
  timezone: string;
  locale: string;
}

export interface UserPreferences {
  theme: "light" | "dark" | "system";
  default_view: string;
  email_alerts: boolean;
  alert_on_node_offline: boolean;
  alert_on_abnormality: boolean;
  watched_nodes: string[];
}

/** UserPublic — the Supabase identity joined to its profiles row. */
export interface AuthUser {
  id: string;
  email: string;
  // Supabase's email_confirmed_at, flattened to a boolean.
  email_verified: boolean;
  role: UserRole;
  status: AccountStatus;
  profile: UserProfile;
  preferences: UserPreferences;
  created_at: string | null;
  updated_at: string | null;
  last_sign_in_at: string | null;
  provider: string | null;
}

export interface AuthSession {
  user: AuthUser;
  // Present for clients without a cookie jar; the browser ignores it and uses
  // the cookies the same response set. Null when Supabase is waiting on the
  // confirmation email, because no session exists yet.
  tokens: {
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
    expires_at: string | null;
  } | null;
  email_confirmation_required: boolean;
}

export class AuthApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AuthApiError";
  }
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/**
 * FastAPI reports errors as {detail}, which is a string for a raised
 * HTTPException and a list of field errors for a failed request model. Both
 * end up in a toast, so both have to come out as one readable sentence.
 */
async function readError(response: Response): Promise<string> {
  const fallback = `Request failed (${response.status})`;

  try {
    const body = await response.json();
    const detail = body?.detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          typeof item?.msg === "string" ? item.msg.replace(/^Value error, /, "") : null,
        )
        .filter(Boolean);
      if (messages.length > 0) return messages.join(". ");
    }

    return fallback;
  } catch {
    return fallback;
  }
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), {
    ...init,
    // Without this the browser withholds the session cookies and every call
    // looks signed out.
    credentials: "include",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
}

/**
 * One call, with a single silent retry through /api/auth/refresh on a 401.
 *
 * Access cookies are short-lived by design, so an expired one is the normal
 * case rather than an error — the refresh cookie turns it back into a live
 * session without the operator noticing. `retry` stops that from recursing when
 * the refresh itself is what failed.
 */
async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  let response = await send(path, init);

  if (response.status === 401 && retry && path !== "/api/auth/refresh") {
    const refreshed = await send("/api/auth/refresh", { method: "POST" });
    if (refreshed.ok) {
      response = await send(path, init);
    }
  }

  if (!response.ok) {
    throw new AuthApiError(await readError(response), response.status);
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// The API
// ---------------------------------------------------------------------------

export function register(input: {
  email: string;
  password: string;
  full_name?: string;
  organization?: string;
}): Promise<AuthSession> {
  return request<AuthSession>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(input: { email: string; password: string }): Promise<AuthSession> {
  return request<AuthSession>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function logout(): Promise<void> {
  // A failure here still means the operator wanted out, so the caller navigates
  // away regardless — the cookies are cleared by the response either way.
  await send("/api/auth/logout", { method: "POST" }).catch(() => undefined);
}

export function logoutEverywhere(): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/logout-all", { method: "POST" });
}

/** The signed-in user, or null when nobody is. Never throws on 401. */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  try {
    return await request<AuthUser>("/api/auth/me");
  } catch (error) {
    if (error instanceof AuthApiError && (error.status === 401 || error.status === 403)) {
      return null;
    }
    throw error;
  }
}

export function updateAccount(input: {
  profile?: Partial<UserProfile>;
  preferences?: Partial<UserPreferences>;
}): Promise<AuthUser> {
  return request<AuthUser>("/api/auth/me", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function changePassword(input: {
  current_password: string;
  new_password: string;
}): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/me/password", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Ask Supabase to email a reset link. Always resolves, whether or not the address exists. */
export function forgotPassword(email: string): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}
