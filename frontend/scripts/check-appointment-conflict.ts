// Focused executable check for app/lib/appointment-conflict.ts (MED-005).
// Run via `npm run check:appointment-conflict`.

import { conflictMessage, decideConflictAction } from "../app/lib/appointment-conflict.ts";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function testNon409IsAlwaysShowError(): void {
  assertEqual(decideConflictAction({ status: 403 }), "show_error", "a 403 must never trigger an availability refresh");
  assertEqual(decideConflictAction({ status: 404 }), "show_error", "a 404 must never trigger an availability refresh");
}

function testAppointmentConflictRefreshesAvailability(): void {
  assertEqual(
    decideConflictAction({ status: 409, code: "appointment_conflict" }),
    "refresh_availability",
    "a genuine DB-level double-booking must prompt a fresh availability fetch",
  );
}

function testOutsideScheduleRefreshesAvailability(): void {
  assertEqual(
    decideConflictAction({ status: 409, code: "outside_schedule" }),
    "refresh_availability",
    "an outside-schedule rejection must prompt a fresh availability fetch",
  );
}

function testStaleVersionReloadsAppointment(): void {
  assertEqual(
    decideConflictAction({ status: 409, code: "stale_version" }),
    "reload_appointment",
    "a stale optimistic-lock version must reload the appointment, not availability",
  );
}

function testUnknownConflictCodeFallsBackToShowError(): void {
  assertEqual(
    decideConflictAction({ status: 409, code: "invalid_status_transition" }),
    "show_error",
    "an invalid-transition conflict has nothing to refresh - must just show the error",
  );
}

function testConflictMessageFallsBackToServerDetailForUnknownCode(): void {
  const message = conflictMessage({ status: 409, code: "some_future_code", detail: "server said this" }, "fallback");
  assertEqual(message, "server said this", "an unrecognized code must surface the server's own detail, not the generic fallback");
}

const checks: Array<[string, () => void]> = [
  ["non-409 is always show_error", testNon409IsAlwaysShowError],
  ["appointment_conflict refreshes availability", testAppointmentConflictRefreshesAvailability],
  ["outside_schedule refreshes availability", testOutsideScheduleRefreshesAvailability],
  ["stale_version reloads appointment", testStaleVersionReloadsAppointment],
  ["unknown conflict code falls back to show_error", testUnknownConflictCodeFallsBackToShowError],
  ["conflict message falls back to server detail for unknown code", testConflictMessageFallsBackToServerDetailForUnknownCode],
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
