// Pure decision logic for how the calendar UI should react to a 409 from
// the appointment API (see app.core.errors.CalendarConflictError's `code`
// values: "appointment_conflict", "outside_schedule",
// "invalid_status_transition", "stale_version"). Kept separate from the
// React components so the decision table itself is directly checkable -
// see scripts/check-appointment-conflict.ts.

export type ConflictLikeError = { status: number; code?: string };

export type ConflictAction = "refresh_availability" | "reload_appointment" | "show_error";

/** What the UI should DO in response to this error - never inferred ad
 * hoc inline in a component, so every call site reacts consistently. */
export function decideConflictAction(error: ConflictLikeError): ConflictAction {
  if (error.status !== 409) {
    return "show_error";
  }
  if (error.code === "stale_version") {
    return "reload_appointment";
  }
  if (error.code === "appointment_conflict" || error.code === "outside_schedule") {
    return "refresh_availability";
  }
  return "show_error";
}

/** What the UI should SAY - a human-readable message per machine code,
 * falling back to the server-supplied `detail` for anything else. */
export function conflictMessage(error: ConflictLikeError & { detail?: string }, fallback: string): string {
  switch (error.code) {
    case "appointment_conflict":
      return "This time was just booked by someone else. Please pick another slot.";
    case "outside_schedule":
      return "This time is outside the provider's schedule, inside a break, or blocked. Please pick another slot.";
    case "stale_version":
      return "This appointment was changed by someone else. Reloading the latest version.";
    case "invalid_status_transition":
      return "This action is no longer valid for the appointment's current status.";
    default:
      return error.detail ?? fallback;
  }
}
