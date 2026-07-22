// Shared helpers for the /settings/* clinic-administration pages.
//
// There is no authentication yet (see ARCHITECTURE.md / SECURITY.md): the
// backend's development-only identity provider is gated behind
// DEVELOPMENT_IDENTITY_ENABLED and requires an X-Dev-User-Id / X-Tenant-Id
// header pair on every request. This module is the one place that reads/
// writes those two values (from localStorage, client-side only) and
// attaches them to every fetch - never invented per-page.

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const USER_ID_STORAGE_KEY = "clinicAdmin.devUserId";
const TENANT_ID_STORAGE_KEY = "clinicAdmin.devTenantId";

export type DevIdentity = {
  userId: string;
  tenantId: string;
};

export function readDevIdentity(): DevIdentity | null {
  if (typeof window === "undefined") {
    return null;
  }
  const userId = window.localStorage.getItem(USER_ID_STORAGE_KEY);
  const tenantId = window.localStorage.getItem(TENANT_ID_STORAGE_KEY);
  if (!userId || !tenantId) {
    return null;
  }
  return { userId, tenantId };
}

export function writeDevIdentity(identity: DevIdentity): void {
  window.localStorage.setItem(USER_ID_STORAGE_KEY, identity.userId);
  window.localStorage.setItem(TENANT_ID_STORAGE_KEY, identity.tenantId);
}

export function clearDevIdentity(): void {
  window.localStorage.removeItem(USER_ID_STORAGE_KEY);
  window.localStorage.removeItem(TENANT_ID_STORAGE_KEY);
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

/** Thin fetch wrapper: attaches the dev-identity headers, parses JSON, and
 * normalizes a non-2xx response into an `ApiError` carrying the backend's
 * `detail` message - callers never need to inspect `Response` directly. */
export async function apiFetch<T>(
  identity: DevIdentity,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Dev-User-Id": identity.userId,
      "X-Tenant-Id": identity.tenantId,
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
