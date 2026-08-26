// Guards for TanStack server functions, backed by the FastAPI auth service.
//
// The check itself is a call to /api/auth/me: this file never decodes a token
// or decides what a role means, it asks the backend and trusts the answer. That
// is what keeps a single authority over sessions now that the frontend has none
// of its own.

import { createMiddleware } from "@tanstack/react-start";
import { getRequest } from "@tanstack/react-start/server";

import { fetchCurrentUser, type AuthUser } from "./auth";

// Server-side fetch needs an absolute URL, so the relative /api used in the
// browser (proxied by vite.config.ts) is not enough here.
const BACKEND_URL = (
  process.env.TELEMETRY_API_BASE_URL ??
  process.env.VITE_TELEMETRY_API_BASE_URL ??
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

/**
 * Refresh the session cookie if it has expired, before the RPC goes out.
 *
 * Server functions are called from the browser, which attaches the session
 * cookie automatically — but an expired access cookie would fail the guard
 * below for a user who is still perfectly signed in. fetchCurrentUser() renews
 * it through the backend when needed, and is a no-op when it doesn't.
 *
 * Register this globally in src/start.ts, or the renewal never runs.
 */
export const keepSessionFresh = createMiddleware({ type: "function" }).client(async ({ next }) => {
  await fetchCurrentUser().catch(() => null);
  return next();
});

/**
 * Reject the call unless the backend recognises the caller's session, and put
 * the resolved user on the handler's context.
 */
export const requireAuth = createMiddleware({ type: "function" }).server(async ({ next }) => {
  const request = getRequest();

  if (!request?.headers) {
    throw new Error("Unauthorized: no request headers available");
  }

  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");

  if (!cookie && !authorization) {
    throw new Error("Unauthorized: no session cookie or bearer token");
  }

  const headers: Record<string, string> = {};
  if (cookie) headers.cookie = cookie;
  if (authorization) headers.authorization = authorization;

  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}/api/auth/me`, { headers });
  } catch {
    // A dead backend must not read as "signed in".
    throw new Error("Unauthorized: the authentication service is unreachable");
  }

  if (!response.ok) {
    throw new Error("Unauthorized: the session was rejected");
  }

  const user = (await response.json()) as AuthUser;

  return next({ context: { user, userId: user.id, role: user.role } });
});
