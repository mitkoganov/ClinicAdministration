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
  return process.env.NODE_ENV !== "production" && readDevIdentity() !== null;
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

  constructor(status: number, detail: string) {
    super(`API request failed (${status}): ${detail}`);
    this.status = status;
    this.detail = detail;
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
    throw new ApiError(response.status, detail);
  }

  return body as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : "unknown error";
}
