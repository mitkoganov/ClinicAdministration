// Typed API client for MED-005 (Appointments and Calendar). Field names
// mirror the backend Pydantic schemas exactly (snake_case, no camelCase
// conversion layer) - the same convention as every other frontend/lib
// module (see app/settings/staff/page.tsx's inline types). Every
// timestamp field is an offset-aware ISO 8601 string; see
// app/lib/calendar-time.ts for parsing/formatting them.

import { apiFetch } from "./api";

export type MembershipRole = "owner" | "manager" | "operator" | "content_editor" | "auditor";

// --- Rooms ------------------------------------------------------------

export type ClinicRoom = {
  id: string;
  name: string;
  code: string;
  description: string | null;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
};

export type ClinicRoomList = { items: ClinicRoom[]; total: number; limit: number; offset: number };

export function listRooms(params: { status?: string; limit?: number; offset?: number } = {}) {
  return apiFetch<ClinicRoomList>(`/api/v1/rooms?${toQuery(params)}`);
}

export function createRoom(payload: { name: string; code: string; description: string | null }) {
  return apiFetch<ClinicRoom>("/api/v1/rooms", { method: "POST", body: JSON.stringify(payload) });
}

export function updateRoom(id: string, payload: { name?: string; description?: string | null }) {
  return apiFetch<ClinicRoom>(`/api/v1/rooms/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deactivateRoom(id: string) {
  return apiFetch<ClinicRoom>(`/api/v1/rooms/${id}/deactivate`, { method: "POST" });
}

// --- Service types ------------------------------------------------------

export type AppointmentServiceType = {
  id: string;
  name: string;
  code: string;
  description: string | null;
  default_duration_minutes: number;
  buffer_before_minutes: number;
  buffer_after_minutes: number;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
};

export type AppointmentServiceTypeList = {
  items: AppointmentServiceType[];
  total: number;
  limit: number;
  offset: number;
};

export function listServiceTypes(params: { status?: string; limit?: number; offset?: number } = {}) {
  return apiFetch<AppointmentServiceTypeList>(`/api/v1/appointment-service-types?${toQuery(params)}`);
}

export function createServiceType(payload: {
  name: string;
  code: string;
  description: string | null;
  default_duration_minutes: number;
  buffer_before_minutes?: number;
  buffer_after_minutes?: number;
}) {
  return apiFetch<AppointmentServiceType>("/api/v1/appointment-service-types", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateServiceType(
  id: string,
  payload: Partial<Omit<AppointmentServiceType, "id" | "status" | "created_at" | "updated_at">>,
) {
  return apiFetch<AppointmentServiceType>(`/api/v1/appointment-service-types/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deactivateServiceType(id: string) {
  return apiFetch<AppointmentServiceType>(`/api/v1/appointment-service-types/${id}/deactivate`, {
    method: "POST",
  });
}

// --- Provider schedules --------------------------------------------------

export type ScheduleBreak = { id: string; start_time: string; end_time: string; label: string | null };

export type ProviderSchedule = {
  id: string;
  provider_user_id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
  effective_from: string;
  effective_until: string | null;
  room_id: string | null;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
};

export type ProviderScheduleWithBreaks = ProviderSchedule & { breaks: ScheduleBreak[] };

export type ProviderScheduleList = {
  items: ProviderSchedule[];
  total: number;
  limit: number;
  offset: number;
};

export function listSchedules(
  params: { provider_user_id?: string; status?: string; limit?: number; offset?: number } = {},
) {
  return apiFetch<ProviderScheduleList>(`/api/v1/provider-schedules?${toQuery(params)}`);
}

export function getSchedule(id: string) {
  return apiFetch<ProviderScheduleWithBreaks>(`/api/v1/provider-schedules/${id}`);
}

export function createSchedule(payload: {
  provider_user_id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
  effective_from: string;
  effective_until?: string | null;
  room_id?: string | null;
  breaks?: Array<{ start_time: string; end_time: string; label?: string | null }>;
}) {
  return apiFetch<ProviderScheduleWithBreaks>("/api/v1/provider-schedules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateSchedule(
  id: string,
  payload: {
    start_time?: string;
    end_time?: string;
    effective_from?: string;
    effective_until?: string | null;
    room_id?: string | null;
    breaks?: Array<{ start_time: string; end_time: string; label?: string | null }>;
  },
) {
  return apiFetch<ProviderScheduleWithBreaks>(`/api/v1/provider-schedules/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deactivateSchedule(id: string) {
  return apiFetch<ProviderScheduleWithBreaks>(`/api/v1/provider-schedules/${id}/deactivate`, {
    method: "POST",
  });
}

// --- Calendar blocks ------------------------------------------------------

export type CalendarBlockType =
  | "leave"
  | "training"
  | "maintenance"
  | "room_closure"
  | "personal"
  | "other";

export type CalendarBlock = {
  id: string;
  provider_user_id: string | null;
  room_id: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  block_type: CalendarBlockType;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export function listBlocks(params: {
  date_from: string;
  date_to: string;
  provider_id?: string;
  room_id?: string;
}) {
  return apiFetch<CalendarBlock[]>(`/api/v1/calendar-blocks?${toQuery(params)}`);
}

export function createBlock(payload: {
  provider_user_id?: string | null;
  room_id?: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  block_type: CalendarBlockType;
}) {
  return apiFetch<CalendarBlock>("/api/v1/calendar-blocks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteBlock(id: string) {
  return apiFetch<void>(`/api/v1/calendar-blocks/${id}`, { method: "DELETE" });
}

// --- Availability -----------------------------------------------------

export type AvailableSlot = { starts_at: string; ends_at: string };

export type Availability = {
  tenant_timezone: string;
  provider_user_id: string;
  service_type_id: string;
  room_id: string | null;
  slots: AvailableSlot[];
};

export function getAvailability(params: {
  provider_id: string;
  service_type_id: string;
  date_from: string;
  date_to: string;
  room_id?: string;
}) {
  return apiFetch<Availability>(`/api/v1/availability?${toQuery(params)}`);
}

// --- Appointments -----------------------------------------------------

export type AppointmentStatus = "scheduled" | "confirmed" | "cancelled" | "completed" | "no_show";

export type Appointment = {
  id: string;
  provider_user_id: string;
  room_id: string | null;
  service_type_id: string;
  starts_at: string;
  ends_at: string;
  status: AppointmentStatus;
  version: number;
  created_at: string;
  updated_at: string;
  cancelled_at: string | null;
  // Present only for CALENDAR_CONTACT_VISIBLE_ROLES or the appointment's
  // own provider - absent (not merely null) for e.g. an auditor viewing
  // someone else's appointment (see app.api.appointments._serialize).
  patient_display_name?: string;
  patient_phone?: string | null;
  patient_email?: string | null;
  notes?: string | null;
  cancellation_reason?: string | null;
  created_by_user_id?: string;
  updated_by_user_id?: string | null;
};

export type AppointmentList = { items: Appointment[]; total: number; limit: number; offset: number };

export function listAppointments(params: {
  date_from?: string;
  date_to?: string;
  provider_id?: string;
  room_id?: string;
  service_type_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}) {
  return apiFetch<AppointmentList>(`/api/v1/appointments?${toQuery(params)}`);
}

export function getAppointment(id: string) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}`);
}

export function createAppointment(payload: {
  provider_user_id: string;
  room_id?: string | null;
  service_type_id: string;
  starts_at: string;
  ends_at: string;
  patient_display_name: string;
  patient_phone?: string | null;
  patient_email?: string | null;
  notes?: string | null;
  override_availability?: boolean;
  override_reason?: string | null;
}) {
  return apiFetch<Appointment>("/api/v1/appointments", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAppointmentMetadata(
  id: string,
  payload: {
    expected_version: number;
    patient_display_name?: string;
    patient_phone?: string | null;
    patient_email?: string | null;
    notes?: string | null;
  },
) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function rescheduleAppointment(
  id: string,
  payload: {
    expected_version: number;
    starts_at: string;
    ends_at: string;
    provider_user_id?: string;
    room_id?: string | null;
    override_availability?: boolean;
    override_reason?: string | null;
  },
) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelAppointment(id: string, payload: { expected_version: number; reason: string }) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}/cancel`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function confirmAppointment(id: string, expected_version: number) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}/confirm`, {
    method: "POST",
    body: JSON.stringify({ expected_version }),
  });
}

export function completeAppointment(id: string, expected_version: number) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}/complete`, {
    method: "POST",
    body: JSON.stringify({ expected_version }),
  });
}

export function markNoShow(id: string, expected_version: number) {
  return apiFetch<Appointment>(`/api/v1/appointments/${id}/no-show`, {
    method: "POST",
    body: JSON.stringify({ expected_version }),
  });
}

// --- shared helpers -----------------------------------------------------

function toQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      search.set(key, String(value));
    }
  }
  return search.toString();
}
