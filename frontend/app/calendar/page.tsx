"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage, ApiError } from "../lib/api";
import {
  addDays,
  formatTimeRange,
  localDateString,
  localDayBoundsUtc,
  localWallClockToUtc,
  localWeekBoundsUtc,
  startOfWeek,
  todayInTimezone,
  weekDates,
} from "../lib/calendar-time";
import { conflictMessage, decideConflictAction } from "../lib/appointment-conflict";
import {
  canCancel,
  canComplete,
  canConfirm,
  canCreateAppointment,
  canMarkNoShow,
  canOverrideAvailability,
  canRescheduleAppointment,
  type AppointmentStatus as PolicyAppointmentStatus,
  type MembershipRole,
} from "../lib/appointment-policy";
import {
  cancelAppointment,
  completeAppointment,
  confirmAppointment,
  createAppointment,
  getAvailability,
  listAppointments,
  listBlocks,
  listRooms,
  listServiceTypes,
  markNoShow,
  rescheduleAppointment,
  type AppointmentServiceType,
  type Appointment,
  type AvailableSlot,
  type CalendarBlock,
  type ClinicRoom,
} from "../lib/calendar-api";
import { IdentityBanner } from "../settings/identity-banner";
import { SessionStatus } from "../settings/session-status";

type Clinic = { role: MembershipRole; timezone: string };
type Me = { user_id: string };

type PageState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; clinic: Clinic; viewerUserId: string; rooms: ClinicRoom[]; serviceTypes: AppointmentServiceType[] };

type DayState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; appointments: Appointment[]; blocks: CalendarBlock[] };

type ViewMode = "day" | "week";

export default function CalendarPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [viewMode, setViewMode] = useState<ViewMode>("day");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState("");
  const [roomFilter, setRoomFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [dayState, setDayState] = useState<DayState>({ kind: "loading" });
  const [showCreateForm, setShowCreateForm] = useState(false);

  const load = useCallback(() => {
    Promise.all([apiFetch<Clinic>("/api/v1/clinic"), apiFetch<Me>("/api/v1/auth/me"), listRooms({ status: "active", limit: 200 }), listServiceTypes({ status: "active", limit: 200 })])
      .then(([clinic, me, rooms, serviceTypes]) => {
        setState({ kind: "loaded", clinic, viewerUserId: me.user_id, rooms: rooms.items, serviceTypes: serviceTypes.items });
        setSelectedDate((current) => current ?? todayInTimezone(clinic.timezone));
      })
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
  }, []);

  useEffect(() => {
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  const loadRange = useCallback(() => {
    if (state.kind !== "loaded" || selectedDate === null) {
      return;
    }
    const { start, end } =
      viewMode === "week"
        ? localWeekBoundsUtc(startOfWeek(selectedDate), state.clinic.timezone)
        : localDayBoundsUtc(selectedDate, state.clinic.timezone);
    const dateFrom = start.toISOString();
    const dateTo = end.toISOString();
    setDayState({ kind: "loading" });
    Promise.all([
      listAppointments({
        date_from: dateFrom,
        date_to: dateTo,
        provider_id: providerFilter || undefined,
        room_id: roomFilter || undefined,
        status: statusFilter || undefined,
        limit: 200,
      }),
      listBlocks({ date_from: dateFrom, date_to: dateTo, provider_id: providerFilter || undefined, room_id: roomFilter || undefined }),
    ])
      .then(([appointments, blocks]) => setDayState({ kind: "loaded", appointments: appointments.items, blocks }))
      .catch((error: unknown) => setDayState({ kind: "error", message: errorMessage(error) }));
  }, [state, selectedDate, viewMode, providerFilter, roomFilter, statusFilter]);

  useEffect(() => {
    queueMicrotask(loadRange);
  }, [loadRange]);

  if (state.kind === "loading" || selectedDate === null) {
    return <p>Loading calendar…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load calendar: {state.message}</p>;
  }

  const { clinic, viewerUserId, rooms, serviceTypes } = state;

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1000 }}>
      <h1>Calendar</h1>
      <SessionStatus />
      <nav style={{ marginBottom: "1rem" }}>
        <Link href="/settings/clinic">Clinic administration</Link>
      </nav>
      <IdentityBanner />

      <p style={{ opacity: 0.8 }}>
        Showing appointments for the clinic&apos;s own timezone ({clinic.timezone}). Your role: {clinic.role}.
      </p>

      <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />

      {viewMode === "day" ? (
        <DayNavigator selectedDate={selectedDate} timezone={clinic.timezone} onChange={setSelectedDate} />
      ) : (
        <WeekNavigator selectedDate={selectedDate} timezone={clinic.timezone} onChange={setSelectedDate} />
      )}

      <Filters
        providerFilter={providerFilter}
        onProviderFilterChange={setProviderFilter}
        roomFilter={roomFilter}
        onRoomFilterChange={setRoomFilter}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        rooms={rooms}
        viewerUserId={viewerUserId}
      />

      {canCreateAppointment(clinic.role) && (
        <div style={{ margin: "1rem 0" }}>
          <button type="button" onClick={() => setShowCreateForm((v) => !v)}>
            {showCreateForm ? "Close new appointment form" : "New appointment"}
          </button>
        </div>
      )}

      {showCreateForm && (
        <CreateAppointmentForm
          selectedDate={selectedDate}
          timezone={clinic.timezone}
          rooms={rooms}
          serviceTypes={serviceTypes}
          viewerRole={clinic.role}
          onCreated={() => {
            setShowCreateForm(false);
            loadRange();
          }}
        />
      )}

      {viewMode === "day" ? (
        <DayContents
          dayState={dayState}
          timezone={clinic.timezone}
          viewerRole={clinic.role}
          viewerUserId={viewerUserId}
          rooms={rooms}
          serviceTypes={serviceTypes}
          selectedDate={selectedDate}
          onChanged={loadRange}
        />
      ) : (
        <WeekContents
          dayState={dayState}
          timezone={clinic.timezone}
          viewerRole={clinic.role}
          viewerUserId={viewerUserId}
          rooms={rooms}
          serviceTypes={serviceTypes}
          weekStart={startOfWeek(selectedDate)}
          onChanged={loadRange}
        />
      )}
    </main>
  );
}

function ViewModeToggle({
  viewMode,
  onChange,
}: {
  viewMode: ViewMode;
  onChange: (mode: ViewMode) => void;
}) {
  return (
    <div role="tablist" aria-label="Calendar view" style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === "day"}
        style={{ fontWeight: viewMode === "day" ? "bold" : "normal" }}
        onClick={() => onChange("day")}
      >
        Day
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === "week"}
        style={{ fontWeight: viewMode === "week" ? "bold" : "normal" }}
        onClick={() => onChange("week")}
      >
        Week
      </button>
    </div>
  );
}

function WeekNavigator({
  selectedDate,
  timezone,
  onChange,
}: {
  selectedDate: string;
  timezone: string;
  onChange: (date: string) => void;
}) {
  const weekStart = startOfWeek(selectedDate);
  const weekEnd = addDays(weekStart, 6);
  return (
    <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "1rem 0" }}>
      <button type="button" onClick={() => onChange(addDays(weekStart, -7))}>
        ← Previous week
      </button>
      <button type="button" onClick={() => onChange(todayInTimezone(timezone))}>
        Today
      </button>
      <input type="date" value={selectedDate} onChange={(e) => e.target.value && onChange(e.target.value)} />
      <button type="button" onClick={() => onChange(addDays(weekStart, 7))}>
        Next week →
      </button>
      <span style={{ opacity: 0.8 }}>
        {weekStart} – {weekEnd}
      </span>
    </div>
  );
}

function DayNavigator({
  selectedDate,
  timezone,
  onChange,
}: {
  selectedDate: string;
  timezone: string;
  onChange: (date: string) => void;
}) {
  return (
    <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "1rem 0" }}>
      <button type="button" onClick={() => onChange(addDays(selectedDate, -1))}>
        ← Previous day
      </button>
      <button type="button" onClick={() => onChange(todayInTimezone(timezone))}>
        Today
      </button>
      <input type="date" value={selectedDate} onChange={(e) => e.target.value && onChange(e.target.value)} />
      <button type="button" onClick={() => onChange(addDays(selectedDate, 1))}>
        Next day →
      </button>
    </div>
  );
}

function Filters({
  providerFilter,
  onProviderFilterChange,
  roomFilter,
  onRoomFilterChange,
  statusFilter,
  onStatusFilterChange,
  rooms,
  viewerUserId,
}: {
  providerFilter: string;
  onProviderFilterChange: (v: string) => void;
  roomFilter: string;
  onRoomFilterChange: (v: string) => void;
  statusFilter: string;
  onStatusFilterChange: (v: string) => void;
  rooms: ClinicRoom[];
  viewerUserId: string;
}) {
  return (
    <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "center", margin: "1rem 0" }}>
      <label>
        Provider (user id){" "}
        <input
          value={providerFilter}
          onChange={(e) => onProviderFilterChange(e.target.value.trim())}
          placeholder="All providers"
          style={{ padding: "0.25rem", minWidth: 260 }}
        />
      </label>
      <button type="button" onClick={() => onProviderFilterChange(viewerUserId)}>
        My appointments
      </button>
      {providerFilter && (
        <button type="button" onClick={() => onProviderFilterChange("")}>
          Clear provider filter
        </button>
      )}
      <label>
        Room{" "}
        <select value={roomFilter} onChange={(e) => onRoomFilterChange(e.target.value)}>
          <option value="">All rooms</option>
          {rooms.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Status{" "}
        <select value={statusFilter} onChange={(e) => onStatusFilterChange(e.target.value)}>
          <option value="">All</option>
          <option value="scheduled">Scheduled</option>
          <option value="confirmed">Confirmed</option>
          <option value="cancelled">Cancelled</option>
          <option value="completed">Completed</option>
          <option value="no_show">No-show</option>
        </select>
      </label>
    </div>
  );
}

function DayContents({
  dayState,
  timezone,
  viewerRole,
  viewerUserId,
  rooms,
  serviceTypes,
  selectedDate,
  onChanged,
}: {
  dayState: DayState;
  timezone: string;
  viewerRole: MembershipRole;
  viewerUserId: string;
  rooms: ClinicRoom[];
  serviceTypes: AppointmentServiceType[];
  selectedDate: string;
  onChanged: () => void;
}) {
  if (dayState.kind === "loading") {
    return <p>Loading appointments…</p>;
  }
  if (dayState.kind === "error") {
    return <p>Could not load appointments: {dayState.message}</p>;
  }
  const roomName = (id: string | null) => (id ? rooms.find((r) => r.id === id)?.name ?? id : "—");
  const serviceTypeName = (id: string) => serviceTypes.find((s) => s.id === id)?.name ?? id;

  const sortedAppointments = [...dayState.appointments].sort((a, b) => a.starts_at.localeCompare(b.starts_at));

  return (
    <>
      <h2>Appointments</h2>
      {sortedAppointments.length === 0 && <p>No appointments on this day.</p>}
      <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.75rem" }}>
        {sortedAppointments.map((appointment) => (
          <AppointmentCard
            key={appointment.id}
            appointment={appointment}
            timezone={timezone}
            viewerRole={viewerRole}
            viewerUserId={viewerUserId}
            roomName={roomName(appointment.room_id)}
            serviceTypeName={serviceTypeName(appointment.service_type_id)}
            rooms={rooms}
            serviceTypes={serviceTypes}
            selectedDate={selectedDate}
            onChanged={onChanged}
          />
        ))}
      </ul>

      <h2 style={{ marginTop: "2rem" }}>Blocked periods</h2>
      {dayState.blocks.length === 0 && <p>No blocked periods on this day.</p>}
      <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        {dayState.blocks.map((block) => (
          <li key={block.id} style={{ border: "1px solid #ccc", padding: "0.5rem", borderRadius: 4 }}>
            {formatTimeRange(block.starts_at, block.ends_at, timezone)} — {block.block_type}: {block.reason}
            {block.room_id && <> ({roomName(block.room_id)})</>}
          </li>
        ))}
      </ul>
    </>
  );
}

function WeekContents({
  dayState,
  timezone,
  viewerRole,
  viewerUserId,
  rooms,
  serviceTypes,
  weekStart,
  onChanged,
}: {
  dayState: DayState;
  timezone: string;
  viewerRole: MembershipRole;
  viewerUserId: string;
  rooms: ClinicRoom[];
  serviceTypes: AppointmentServiceType[];
  weekStart: string;
  onChanged: () => void;
}) {
  if (dayState.kind === "loading") {
    return <p>Loading appointments…</p>;
  }
  if (dayState.kind === "error") {
    return <p>Could not load appointments: {dayState.message}</p>;
  }
  const roomName = (id: string | null) => (id ? rooms.find((r) => r.id === id)?.name ?? id : "—");
  const serviceTypeName = (id: string) => serviceTypes.find((s) => s.id === id)?.name ?? id;

  const dates = weekDates(weekStart);
  const appointmentsByDate = new Map<string, Appointment[]>();
  const blocksByDate = new Map<string, CalendarBlock[]>();
  for (const date of dates) {
    appointmentsByDate.set(date, []);
    blocksByDate.set(date, []);
  }
  for (const appointment of dayState.appointments) {
    const date = localDateString(appointment.starts_at, timezone);
    appointmentsByDate.get(date)?.push(appointment);
  }
  for (const block of dayState.blocks) {
    const date = localDateString(block.starts_at, timezone);
    blocksByDate.get(date)?.push(block);
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: "0.75rem",
        marginTop: "1rem",
      }}
    >
      {dates.map((date) => {
        const appointments = [...(appointmentsByDate.get(date) ?? [])].sort((a, b) =>
          a.starts_at.localeCompare(b.starts_at),
        );
        const blocks = blocksByDate.get(date) ?? [];
        return (
          <div key={date} style={{ border: "1px solid #ddd", borderRadius: 4, padding: "0.5rem" }}>
            <h3 style={{ marginTop: 0 }}>{date}</h3>
            {appointments.length === 0 && blocks.length === 0 && (
              <p style={{ opacity: 0.7, fontSize: "0.85rem" }}>Nothing scheduled.</p>
            )}
            <ul style={{ listStyle: "none", padding: 0, display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {appointments.map((appointment) => (
                <AppointmentCard
                  key={appointment.id}
                  appointment={appointment}
                  timezone={timezone}
                  viewerRole={viewerRole}
                  viewerUserId={viewerUserId}
                  roomName={roomName(appointment.room_id)}
                  serviceTypeName={serviceTypeName(appointment.service_type_id)}
                  rooms={rooms}
                  serviceTypes={serviceTypes}
                  selectedDate={date}
                  onChanged={onChanged}
                />
              ))}
            </ul>
            {blocks.map((block) => (
              <p key={block.id} style={{ fontSize: "0.8rem", opacity: 0.8 }}>
                {formatTimeRange(block.starts_at, block.ends_at, timezone)} — {block.block_type}
              </p>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function AppointmentCard({
  appointment,
  timezone,
  viewerRole,
  viewerUserId,
  roomName,
  serviceTypeName,
  rooms,
  serviceTypes,
  selectedDate,
  onChanged,
}: {
  appointment: Appointment;
  timezone: string;
  viewerRole: MembershipRole;
  viewerUserId: string;
  roomName: string;
  serviceTypeName: string;
  rooms: ClinicRoom[];
  serviceTypes: AppointmentServiceType[];
  selectedDate: string;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showReschedule, setShowReschedule] = useState(false);

  const policyCtx = {
    viewerRole,
    viewerUserId,
    providerUserId: appointment.provider_user_id,
    status: appointment.status as PolicyAppointmentStatus,
  };

  async function runAction(action: () => Promise<Appointment>) {
    setPending(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const decision = decideConflictAction(err);
        if (decision === "reload_appointment") {
          onChanged();
        }
        setError(conflictMessage(err, errorMessage(err)));
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setPending(false);
    }
  }

  async function handleCancel() {
    const reason = window.prompt("Cancellation reason:");
    if (!reason || !reason.trim()) {
      return;
    }
    await runAction(() => cancelAppointment(appointment.id, { expected_version: appointment.version, reason: reason.trim() }));
  }

  return (
    <li style={{ border: "1px solid #ccc", padding: "0.75rem", borderRadius: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{formatTimeRange(appointment.starts_at, appointment.ends_at, timezone)}</strong>
        <span>{appointment.status}</span>
      </div>
      <div>Provider: {appointment.provider_user_id}</div>
      <div>Room: {roomName}</div>
      <div>Service: {serviceTypeName}</div>
      <div>Patient: {appointment.patient_display_name}</div>
      {appointment.patient_phone !== undefined && appointment.patient_phone && <div>Phone: {appointment.patient_phone}</div>}
      {appointment.patient_phone === undefined && <div style={{ opacity: 0.7 }}>Contact details hidden for your role.</div>}
      {appointment.notes && <div>Notes: {appointment.notes}</div>}
      {appointment.cancellation_reason && <div>Cancellation reason: {appointment.cancellation_reason}</div>}

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem", flexWrap: "wrap" }}>
        {canConfirm(policyCtx) && (
          <button type="button" disabled={pending} onClick={() => runAction(() => confirmAppointment(appointment.id, appointment.version))}>
            Confirm
          </button>
        )}
        {canComplete(policyCtx) && (
          <button type="button" disabled={pending} onClick={() => runAction(() => completeAppointment(appointment.id, appointment.version))}>
            Mark completed
          </button>
        )}
        {canMarkNoShow(policyCtx) && (
          <button type="button" disabled={pending} onClick={() => runAction(() => markNoShow(appointment.id, appointment.version))}>
            Mark no-show
          </button>
        )}
        {canRescheduleAppointment(policyCtx) && (
          <button type="button" disabled={pending} onClick={() => setShowReschedule((v) => !v)}>
            {showReschedule ? "Close reschedule" : "Reschedule"}
          </button>
        )}
        {canCancel(policyCtx) && appointment.status !== "cancelled" && (
          <button type="button" disabled={pending} onClick={handleCancel}>
            Cancel
          </button>
        )}
      </div>

      {error && <p role="alert">{error}</p>}

      {showReschedule && (
        <RescheduleForm
          appointment={appointment}
          timezone={timezone}
          rooms={rooms}
          serviceTypes={serviceTypes}
          viewerRole={viewerRole}
          selectedDate={selectedDate}
          onDone={() => {
            setShowReschedule(false);
            onChanged();
          }}
        />
      )}
    </li>
  );
}

function RescheduleForm({
  appointment,
  timezone,
  rooms,
  viewerRole,
  selectedDate,
  onDone,
}: {
  appointment: Appointment;
  timezone: string;
  rooms: ClinicRoom[];
  serviceTypes: AppointmentServiceType[];
  viewerRole: MembershipRole;
  selectedDate: string;
  onDone: () => void;
}) {
  const [date, setDate] = useState(selectedDate);
  const [slots, setSlots] = useState<AvailableSlot[] | null>(null);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [override, setOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const durationMs = useMemo(
    () => new Date(appointment.ends_at).getTime() - new Date(appointment.starts_at).getTime(),
    [appointment.starts_at, appointment.ends_at],
  );

  async function loadSlots() {
    setLoadingSlots(true);
    setError(null);
    try {
      const availability = await getAvailability({
        provider_id: appointment.provider_user_id,
        service_type_id: appointment.service_type_id,
        date_from: date,
        date_to: date,
        room_id: appointment.room_id ?? undefined,
      });
      setSlots(availability.slots);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setLoadingSlots(false);
    }
  }

  async function submitReschedule(startsAt: string) {
    setSubmitting(true);
    setError(null);
    try {
      const endsAt = new Date(new Date(startsAt).getTime() + durationMs).toISOString();
      await rescheduleAppointment(appointment.id, {
        expected_version: appointment.version,
        starts_at: startsAt,
        ends_at: endsAt,
        override_availability: override,
        override_reason: override ? overrideReason : undefined,
      });
      onDone();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(conflictMessage(err, errorMessage(err)));
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ marginTop: "0.75rem", padding: "0.5rem", border: "1px dashed #999" }}>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        <button type="button" disabled={loadingSlots} onClick={loadSlots}>
          {loadingSlots ? "Loading slots…" : "Find available slots"}
        </button>
      </div>
      {slots !== null && slots.length === 0 && <p>No available slots on this day.</p>}
      {slots !== null && slots.length > 0 && (
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.5rem" }}>
          {slots.map((slot) => (
            <button key={slot.starts_at} type="button" disabled={submitting} onClick={() => submitReschedule(slot.starts_at)}>
              {formatTimeRange(slot.starts_at, slot.ends_at, timezone)}
            </button>
          ))}
        </div>
      )}
      {canOverrideAvailability(viewerRole) && (
        <div style={{ marginTop: "0.5rem" }}>
          <label>
            <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} /> Override
            availability (requires a reason)
          </label>
          {override && (
            <input
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="Override reason"
              style={{ marginLeft: "0.5rem", padding: "0.25rem", minWidth: 240 }}
            />
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      <p style={{ fontSize: "0.85rem", opacity: 0.8 }}>Room: {appointment.room_id ? rooms.find((r) => r.id === appointment.room_id)?.name ?? appointment.room_id : "none"}</p>
    </div>
  );
}

function CreateAppointmentForm({
  selectedDate,
  timezone,
  rooms,
  serviceTypes,
  viewerRole,
  onCreated,
}: {
  selectedDate: string;
  timezone: string;
  rooms: ClinicRoom[];
  serviceTypes: AppointmentServiceType[];
  viewerRole: MembershipRole;
  onCreated: () => void;
}) {
  const [providerUserId, setProviderUserId] = useState("");
  const [serviceTypeId, setServiceTypeId] = useState(serviceTypes[0]?.id ?? "");
  const [roomId, setRoomId] = useState("");
  const [date, setDate] = useState(selectedDate);
  const [slots, setSlots] = useState<AvailableSlot[] | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<AvailableSlot | null>(null);
  const [manualTime, setManualTime] = useState("");
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [patientName, setPatientName] = useState("");
  const [patientPhone, setPatientPhone] = useState("");
  const [patientEmail, setPatientEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [override, setOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const serviceType = serviceTypes.find((s) => s.id === serviceTypeId);

  async function loadSlots() {
    if (!providerUserId.trim() || !serviceTypeId) {
      setError("Provider and service type are required before loading slots.");
      return;
    }
    setLoadingSlots(true);
    setError(null);
    try {
      const availability = await getAvailability({
        provider_id: providerUserId.trim(),
        service_type_id: serviceTypeId,
        date_from: date,
        date_to: date,
        room_id: roomId || undefined,
      });
      setSlots(availability.slots);
      setSelectedSlot(null);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setLoadingSlots(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!providerUserId.trim() || !serviceTypeId || !patientName.trim()) {
      setError("Provider, service type, and patient name are required.");
      return;
    }
    let startsAt: string;
    let endsAt: string;
    if (selectedSlot) {
      startsAt = selectedSlot.starts_at;
      endsAt = selectedSlot.ends_at;
    } else if (manualTime && serviceType) {
      const start = localWallClockToUtc(date, manualTime, timezone);
      startsAt = start.toISOString();
      endsAt = new Date(start.getTime() + serviceType.default_duration_minutes * 60_000).toISOString();
    } else {
      setError("Pick an available slot or enter a manual time.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await createAppointment({
        provider_user_id: providerUserId.trim(),
        room_id: roomId || null,
        service_type_id: serviceTypeId,
        starts_at: startsAt,
        ends_at: endsAt,
        patient_display_name: patientName.trim(),
        patient_phone: patientPhone.trim() || null,
        patient_email: patientEmail.trim() || null,
        notes: notes.trim() || null,
        override_availability: override,
        override_reason: override ? overrideReason.trim() : undefined,
      });
      onCreated();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const decision = decideConflictAction(err);
        if (decision === "refresh_availability") {
          setSlots(null);
          setSelectedSlot(null);
        }
        setError(conflictMessage(err, errorMessage(err)));
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ border: "1px solid #ccc", padding: "1rem", borderRadius: 4, marginBottom: "1rem" }}>
      <h3>New appointment</h3>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.5rem" }}>
        <label>
          Provider (user id){" "}
          <input value={providerUserId} onChange={(e) => setProviderUserId(e.target.value)} style={{ padding: "0.25rem", minWidth: 260 }} />
        </label>
        <label>
          Service type{" "}
          <select value={serviceTypeId} onChange={(e) => setServiceTypeId(e.target.value)}>
            {serviceTypes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.default_duration_minutes} min)
              </option>
            ))}
          </select>
        </label>
        <label>
          Room{" "}
          <select value={roomId} onChange={(e) => setRoomId(e.target.value)}>
            <option value="">None</option>
            {rooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Date <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        </label>
      </div>

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.5rem" }}>
        <button type="button" disabled={loadingSlots} onClick={loadSlots}>
          {loadingSlots ? "Loading slots…" : "Find available slots"}
        </button>
        <span>or manual time (HH:MM):</span>
        <input value={manualTime} onChange={(e) => setManualTime(e.target.value)} placeholder="14:30" style={{ width: 80 }} />
      </div>

      {slots !== null && slots.length === 0 && <p>No available slots on this day.</p>}
      {slots !== null && slots.length > 0 && (
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.5rem" }}>
          {slots.map((slot) => (
            <button
              key={slot.starts_at}
              type="button"
              onClick={() => setSelectedSlot(slot)}
              style={{ fontWeight: selectedSlot?.starts_at === slot.starts_at ? "bold" : "normal" }}
            >
              {formatTimeRange(slot.starts_at, slot.ends_at, timezone)}
            </button>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.5rem" }}>
        <input value={patientName} onChange={(e) => setPatientName(e.target.value)} placeholder="Patient name (required)" style={{ padding: "0.25rem", minWidth: 220 }} />
        <input value={patientPhone} onChange={(e) => setPatientPhone(e.target.value)} placeholder="Patient phone" style={{ padding: "0.25rem", minWidth: 160 }} />
        <input value={patientEmail} onChange={(e) => setPatientEmail(e.target.value)} placeholder="Patient email" style={{ padding: "0.25rem", minWidth: 220 }} />
      </div>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes" style={{ width: "100%", marginBottom: "0.5rem" }} />

      {canOverrideAvailability(viewerRole) && (
        <div style={{ marginBottom: "0.5rem" }}>
          <label>
            <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} /> Override
            availability (requires a reason)
          </label>
          {override && (
            <input
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="Override reason"
              style={{ marginLeft: "0.5rem", padding: "0.25rem", minWidth: 240 }}
            />
          )}
        </div>
      )}

      <button type="submit" disabled={submitting}>
        {submitting ? "Creating…" : "Create appointment"}
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
