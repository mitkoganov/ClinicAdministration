// Focused executable check for app/lib/appointment-policy.ts (MED-005).
// There is no frontend test framework in this repository - see
// check-clinic-selection.ts for the established pattern this follows.
// Run via `npm run check:appointment-policy`.

import {
  canCancel,
  canComplete,
  canConfirm,
  canMarkNoShow,
  canRescheduleAppointment,
  canTransition,
  type AppointmentActionContext,
} from "../app/lib/appointment-policy.ts";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function ctx(overrides: Partial<AppointmentActionContext>): AppointmentActionContext {
  return {
    viewerRole: "operator",
    viewerUserId: "viewer",
    providerUserId: "provider",
    status: "scheduled",
    ...overrides,
  };
}

function testTerminalStatusesHaveNoTransitions(): void {
  for (const status of ["cancelled", "completed", "no_show"] as const) {
    for (const target of ["scheduled", "confirmed", "cancelled", "completed", "no_show"] as const) {
      assertEqual(canTransition(status, target), false, `${status} -> ${target} must never be allowed`);
    }
  }
}

function testAuditorCannotCancel(): void {
  assertEqual(canCancel(ctx({ viewerRole: "auditor" })), false, "auditor must never cancel someone else's appointment");
}

function testOwnerCanCancelScheduled(): void {
  assertEqual(canCancel(ctx({ viewerRole: "owner" })), true, "owner must be able to cancel a scheduled appointment");
}

function testCancelIsAlwaysShownOnAlreadyCancelled(): void {
  assertEqual(
    canCancel(ctx({ viewerRole: "owner", status: "cancelled" })),
    true,
    "cancel must stay available (idempotent) on an already-cancelled appointment",
  );
}

function testProviderCanCompleteOwnAppointmentEvenAsAuditor(): void {
  // "Provider" is a fact (this row's provider_user_id), not a role -
  // self-scoped complete/no-show must work regardless of the viewer's
  // membership role, mirroring AppointmentService._transition's
  // allow_self_scoped=True paths.
  assertEqual(
    canComplete(ctx({ viewerRole: "auditor", viewerUserId: "same", providerUserId: "same" })),
    true,
    "a provider must be able to complete their own appointment even with the auditor role",
  );
}

function testAuditorCannotCompleteSomeoneElsesAppointment(): void {
  assertEqual(
    canComplete(ctx({ viewerRole: "auditor", viewerUserId: "viewer", providerUserId: "someone-else" })),
    false,
    "auditor must not complete another provider's appointment",
  );
}

function testProviderCannotCancelOwnAppointmentWithoutWriteRole(): void {
  // Cancel deliberately has allow_self_scoped=False server-side (task.md
  // "cancel" policy) - unlike complete/no-show, a provider cannot cancel
  // their own appointment purely by being the provider.
  assertEqual(
    canCancel(ctx({ viewerRole: "auditor", viewerUserId: "same", providerUserId: "same" })),
    false,
    "self-scoped cancel must still require a CALENDAR_WRITE_ROLES role",
  );
}

function testOnlyReschedulableStatusesOfferReschedule(): void {
  assertEqual(canRescheduleAppointment(ctx({ viewerRole: "owner", status: "completed" })), false, "a completed appointment must not offer reschedule");
  assertEqual(canRescheduleAppointment(ctx({ viewerRole: "owner", status: "scheduled" })), true, "a scheduled appointment must offer reschedule to a write-role viewer");
}

function testConfirmRequiresWriteRoleEvenForSelf(): void {
  assertEqual(
    canConfirm(ctx({ viewerRole: "auditor", viewerUserId: "same", providerUserId: "same" })),
    false,
    "confirm has no self-scoped bypass server-side - must still require a write role",
  );
}

function testNoShowSelfScopedBypassMirrorsComplete(): void {
  assertEqual(
    canMarkNoShow(ctx({ viewerRole: "auditor", viewerUserId: "same", providerUserId: "same" })),
    true,
    "no-show must allow the same self-scoped bypass as complete",
  );
}

const checks: Array<[string, () => void]> = [
  ["terminal statuses have no outgoing transitions", testTerminalStatusesHaveNoTransitions],
  ["auditor cannot cancel someone else's appointment", testAuditorCannotCancel],
  ["owner can cancel a scheduled appointment", testOwnerCanCancelScheduled],
  ["cancel stays available on an already-cancelled appointment", testCancelIsAlwaysShownOnAlreadyCancelled],
  ["provider can complete own appointment even as auditor", testProviderCanCompleteOwnAppointmentEvenAsAuditor],
  ["auditor cannot complete someone else's appointment", testAuditorCannotCompleteSomeoneElsesAppointment],
  ["self-scoped cancel still requires a write role", testProviderCannotCancelOwnAppointmentWithoutWriteRole],
  ["only reschedulable statuses offer reschedule", testOnlyReschedulableStatusesOfferReschedule],
  ["confirm requires write role even for self", testConfirmRequiresWriteRoleEvenForSelf],
  ["no-show self-scoped bypass mirrors complete", testNoShowSelfScopedBypassMirrorsComplete],
];

let failures = 0;
for (const [name, check] of checks) {
  try {
    check();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log(`${checks.length} check(s) passed`);
