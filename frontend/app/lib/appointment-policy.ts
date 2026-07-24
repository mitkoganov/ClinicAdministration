// Pure, dependency-free UI policy helpers for MED-005 (Appointments and
// Calendar) - mirrors app.core.authorization's CALENDAR_* role sets and
// app.services.appointment_service._ALLOWED_TRANSITIONS exactly, for
// hiding actions a caller could never actually perform. This is a
// usability convenience ONLY: the backend independently re-derives and
// enforces every one of these rules regardless of what the UI shows or
// hides (see every app.services.*_service module).

export type MembershipRole = "owner" | "manager" | "operator" | "content_editor" | "auditor";
export type AppointmentStatus = "scheduled" | "confirmed" | "cancelled" | "completed" | "no_show";

export const CALENDAR_READ_ROLES: ReadonlySet<MembershipRole> = new Set([
  "owner",
  "manager",
  "operator",
  "auditor",
]);
export const CALENDAR_WRITE_ROLES: ReadonlySet<MembershipRole> = new Set([
  "owner",
  "manager",
  "operator",
]);
export const CALENDAR_CONFIG_ROLES: ReadonlySet<MembershipRole> = new Set(["owner", "manager"]);
export const CALENDAR_OVERRIDE_ROLES: ReadonlySet<MembershipRole> = new Set(["owner", "manager"]);
export const CALENDAR_CONTACT_VISIBLE_ROLES: ReadonlySet<MembershipRole> = new Set([
  "owner",
  "manager",
  "operator",
]);

const ALLOWED_TRANSITIONS: Record<AppointmentStatus, ReadonlySet<AppointmentStatus>> = {
  scheduled: new Set(["confirmed", "cancelled", "completed", "no_show"]),
  confirmed: new Set(["cancelled", "completed", "no_show"]),
  cancelled: new Set(),
  completed: new Set(),
  no_show: new Set(),
};

const RESCHEDULABLE_STATUSES: ReadonlySet<AppointmentStatus> = new Set(["scheduled", "confirmed"]);

export function canTransition(from: AppointmentStatus, to: AppointmentStatus): boolean {
  return ALLOWED_TRANSITIONS[from].has(to);
}

export function canReschedule(status: AppointmentStatus): boolean {
  return RESCHEDULABLE_STATUSES.has(status);
}

/** Any active member may always act on their OWN calendar (as the
 * provider), regardless of role - a bare identity check, not a
 * permission grant (see app.services.appointment_service). */
export function isSelfScoped(viewerUserId: string, providerUserId: string): boolean {
  return viewerUserId === providerUserId;
}

export type AppointmentActionContext = {
  viewerRole: MembershipRole;
  viewerUserId: string;
  providerUserId: string;
  status: AppointmentStatus;
};

export function canCreateAppointment(viewerRole: MembershipRole): boolean {
  return CALENDAR_WRITE_ROLES.has(viewerRole);
}

export function canOverrideAvailability(viewerRole: MembershipRole): boolean {
  return CALENDAR_OVERRIDE_ROLES.has(viewerRole);
}

export function canSeePatientContact(ctx: AppointmentActionContext): boolean {
  return CALENDAR_CONTACT_VISIBLE_ROLES.has(ctx.viewerRole) || isSelfScoped(ctx.viewerUserId, ctx.providerUserId);
}

export function canCancel(ctx: AppointmentActionContext): boolean {
  if (ctx.status === "cancelled") {
    // Cancel is idempotent server-side - always shown as available for a
    // caller who could reach this appointment at all, so a retried click
    // on an already-cancelled row never surfaces as a dead end.
    return true;
  }
  return canTransition(ctx.status, "cancelled") && CALENDAR_WRITE_ROLES.has(ctx.viewerRole);
}

export function canConfirm(ctx: AppointmentActionContext): boolean {
  return canTransition(ctx.status, "confirmed") && CALENDAR_WRITE_ROLES.has(ctx.viewerRole);
}

export function canComplete(ctx: AppointmentActionContext): boolean {
  if (!canTransition(ctx.status, "completed")) {
    return false;
  }
  return CALENDAR_WRITE_ROLES.has(ctx.viewerRole) || isSelfScoped(ctx.viewerUserId, ctx.providerUserId);
}

export function canMarkNoShow(ctx: AppointmentActionContext): boolean {
  if (!canTransition(ctx.status, "no_show")) {
    return false;
  }
  return CALENDAR_WRITE_ROLES.has(ctx.viewerRole) || isSelfScoped(ctx.viewerUserId, ctx.providerUserId);
}

export function canRescheduleAppointment(ctx: AppointmentActionContext): boolean {
  return canReschedule(ctx.status) && CALENDAR_WRITE_ROLES.has(ctx.viewerRole);
}
