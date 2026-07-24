// Shared API client for the whole app: session-cookie authentication
// (never a token in localStorage - see ARCHITECTURE.md/SECURITY.md) plus
// the CSRF double-submit header every mutating request needs.
//
// The development-only identity mechanism (X-Dev-User-Id/X-Tenant-Id,
// stored in localStorage purely for local testing convenience) is
// attached automatically ONLY when present - a real session cookie
// always takes priority server-side regardless (see
// app.core.tenant_context.get_tenant_context), so this never overrides
// or interferes with normal session-based authentication.

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const DEV_USER_ID_KEY = "clinicAdmin.devUserId";
const DEV_TENANT_ID_KEY = "clinicAdmin.devTenantId";
const CSRF_COOKIE_NAME = "csrf_token";
const CSRF_HEADER_NAME = "X-CSRF-Token";

export type DevIdentity = {
  userId: string;
  tenantId: string;
};

export function readDevIdentity(): DevIdentity | null {
  if (typeof window === "undefined") {
    return null;
  }
  const userId = window.localStorage.getItem(DEV_USER_ID_KEY);
  const tenantId = window.localStorage.getItem(DEV_TENANT_ID_KEY);
  if (!userId || !tenantId) {
    return null;
  }
  return { userId, tenantId };
}

export function writeDevIdentity(identity: DevIdentity): void {
  window.localStorage.setItem(DEV_USER_ID_KEY, identity.userId);
  window.localStorage.setItem(DEV_TENANT_ID_KEY, identity.tenantId);
}

export function clearDevIdentity(): void {
  window.localStorage.removeItem(DEV_USER_ID_KEY);
  window.localStorage.removeItem(DEV_TENANT_ID_KEY);
}

// Single centralized place for every "is the development-identity path
// available/active, and where should an unauthenticated caller go"
// decision - every other file (session-status.tsx, login/page.tsx, the
// /dev/identity route) calls into these instead of re-deriving
// NODE_ENV/localStorage/redirect logic itself. Splitting this into two
// atomic checks (build-time availability vs. whether one has actually
// been configured) is what lets a clean browser be routed to the
// dev-identity *entry point* instead of either being stuck on /login
// (there is no session-based fallback in development) or silently doing
// nothing (a configured-only check would never fire for a first-time
// visitor). The actual decision table lives in the dependency-free
// app/lib/dev-identity-policy.ts (see its focused executable check) -
// this file only supplies that pure function with the real
// process.env/localStorage inputs.

export { DEV_IDENTITY_ENTRY_PATH } from "./dev-identity-policy";
import { resolveUnauthenticatedDestination } from "./dev-identity-policy";

/** Build-time-only: whether the retained development-identity mechanism
 * exists in this build at all, independent of whether anyone has
 * configured one yet. Mirrors the backend's own
 * `ENVIRONMENT=development` half of `DEVELOPMENT_IDENTITY_ENABLED`'s
 * startup gate (see app.core.config.Settings) - deliberately NOT
 * derived from localStorage, which is client-controlled and must never
 * be the thing that decides whether this mechanism exists. */
export function isDevelopmentIdentityAvailable(): boolean {
  return process.env.NODE_ENV !== "production";
}

/** Whether a caller has actually configured a dev identity via the
 * banner/entry-point selector (see identity-banner.tsx). Says nothing
 * about whether the mechanism is even available in this build - always
 * combine with `isDevelopmentIdentityAvailable()` before treating this
 * as meaningful, since a value that somehow survived into a production
 * bundle must never be honored on its own (see `isDevIdentityModeActive`). */
export function hasConfiguredDevIdentity(): boolean {
  return readDevIdentity() !== null;
}

/** Whether the retained development-identity path should be treated as
 * active for frontend *routing* decisions (e.g. "should a 401 from
 * `/auth/me` redirect to `/login`?"). Deliberately requires BOTH a
 * build-time development environment AND an explicitly configured
 * identity - never localStorage alone - mirroring the backend's own
 * `DEVELOPMENT_IDENTITY_ENABLED` + `ENVIRONMENT=development` gate (see
 * app.core.config.Settings). A localStorage value surviving into a
 * production build is never enough by itself: `IdentityBanner` already
 * never renders (and thus never writes one) in a production build, and
 * this check independently refuses to honor one anyway. This never
 * grants access on its own - the backend independently re-validates any
 * dev header against the database regardless of what the frontend
 * believes about "dev mode". */
export function isDevIdentityModeActive(): boolean {
  return isDevelopmentIdentityAvailable() && hasConfiguredDevIdentity();
}

/** Where an unauthenticated (401 from `/auth/me`) caller should go, or
 * `null` if they should stay put because the dev-identity path can
 * already serve them. The single decision point behind every
 * `/settings/*` page's unauthenticated handling:
 * - production build → `/login` (no dev path exists at all);
 * - development build, no dev identity configured yet → the dev-identity
 *   entry point, NOT `/login` - a clean browser must be able to reach the
 *   selector without first needing a production session it can never
 *   have in this mode;
 * - development build, dev identity already configured → `null` - the
 *   caller's `apiFetch` calls already attach those headers regardless of
 *   this component ever mounting, so there is nothing to redirect to. */
export function getUnauthenticatedDestination(): string | null {
  return resolveUnauthenticatedDestination({
    developmentIdentityAvailable: isDevelopmentIdentityAvailable(),
    configuredDevIdentity: hasConfiguredDevIdentity(),
  });
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  /** The optional machine-readable error code some endpoints add
   * alongside `detail` (see app.core.errors.AppError's `code` field,
   * e.g. "appointment_conflict"/"outside_schedule"/"stale_version" from
   * MED-005) - `undefined` for every error response that doesn't set one. */
  code?: string;

  constructor(status: number, detail: string, code?: string) {
    super(`API request failed (${status}): ${detail}`);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

/** Thin fetch wrapper used by every page: always sends the session cookie
 * (`credentials: "include"`), always attaches the CSRF header for
 * mutating requests when a CSRF cookie is present, and attaches the
 * development-identity headers only when one has been explicitly set via
 * the dev-identity banner (see app/settings/identity-banner.tsx) - never
 * required, never a substitute for a real session. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const devIdentity = readDevIdentity();
  const csrfToken = readCookie(CSRF_COOKIE_NAME);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { [CSRF_HEADER_NAME]: csrfToken } : {}),
      ...(devIdentity
        ? { "X-Dev-User-Id": devIdentity.userId, "X-Tenant-Id": devIdentity.tenantId }
        : {}),
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `backend responded with ${response.status}`;
    const code =
      body && typeof body === "object" && "code" in body
        ? String((body as { code: unknown }).code)
        : undefined;
    throw new ApiError(response.status, detail, code);
  }

  return body as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : "unknown error";
}
