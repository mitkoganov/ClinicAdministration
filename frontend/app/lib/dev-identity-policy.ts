// Pure decision logic behind "where should an unauthenticated /settings/*
// caller go" (MED-004 repair, finding 2). Deliberately dependency-free
// (no process.env, no localStorage, no routing) so it can be reasoned
// about and executed in isolation, the same way
// app/lib/clinic-selection.ts's decideClinicSelection is - there is no
// frontend test framework in this repository, so this file's behavior is
// what a focused executable check (scripts/check-dev-identity-policy.ts)
// verifies directly, alongside typecheck/build. app/lib/api.ts's
// `getUnauthenticatedDestination` is the thin wrapper that supplies the
// real build-time/localStorage inputs to this function - it is never
// re-implemented anywhere else.

export const DEV_IDENTITY_ENTRY_PATH = "/dev/identity";
export const LOGIN_PATH = "/login";

export type UnauthenticatedRoutingInputs = {
  /** Build-time-only: does the retained development-identity mechanism
   * exist in this build at all (see `isDevelopmentIdentityAvailable` in
   * app/lib/api.ts)? Never derived from anything client-controlled. */
  developmentIdentityAvailable: boolean;
  /** Has a caller actually configured a dev identity via the selector
   * (see `hasConfiguredDevIdentity` in app/lib/api.ts)? Meaningless on
   * its own - only consulted when the mechanism is available at all. */
  configuredDevIdentity: boolean;
};

/** `null` means "stay put" - the caller already has everything they need
 * (either a real session that made this whole question moot, or an
 * already-configured dev identity whose headers `apiFetch` attaches
 * regardless of this decision ever running again). */
export function resolveUnauthenticatedDestination(
  inputs: UnauthenticatedRoutingInputs,
): string | null {
  if (!inputs.developmentIdentityAvailable) {
    return LOGIN_PATH;
  }
  if (inputs.configuredDevIdentity) {
    return null;
  }
  return DEV_IDENTITY_ENTRY_PATH;
}
