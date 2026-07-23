// Pure decision logic for the post-login clinic-selection step (MED-004
// repair, finding 1). A brand-new session always starts with no clinic
// selected server-side (see ARCHITECTURE.md - "Session lifecycle and
// cookies"), so the frontend must decide what to do with the caller's
// list of active clinic memberships before it can reach any tenant-scoped
// page. Kept as a standalone, dependency-free module (no fetch, no
// routing, no React) so it can be reasoned about and executed in
// isolation - there is no frontend test framework in this repository, so
// this file's behavior is what a focused executable check
// (scripts/check-clinic-selection.mjs) verifies directly, alongside
// typecheck/build.

export type ClinicSummary = {
  tenant_id: string;
  name: string;
  role: string;
};

export type ClinicSelectionDecision =
  | { kind: "none" }
  | { kind: "auto"; clinic: ClinicSummary }
  | { kind: "choose"; clinics: ClinicSummary[] };

/** Never chooses on behalf of the caller when there is more than one
 * option - only a single active membership is unambiguous enough to
 * select without asking. The backend's own `/api/v1/auth/select-clinic`
 * independently re-validates whatever tenant id is ultimately submitted
 * (active tenant, active membership) - this function only decides what
 * UI state to show, it is never itself an authorization decision. */
export function decideClinicSelection(clinics: ClinicSummary[]): ClinicSelectionDecision {
  if (clinics.length === 0) {
    return { kind: "none" };
  }
  if (clinics.length === 1) {
    return { kind: "auto", clinic: clinics[0] };
  }
  return { kind: "choose", clinics };
}
