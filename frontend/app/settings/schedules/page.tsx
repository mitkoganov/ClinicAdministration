"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "../../lib/api";
import {
  createSchedule,
  deactivateSchedule,
  listRooms,
  listSchedules,
  type ClinicRoom,
  type ProviderSchedule,
} from "../../lib/calendar-api";

type ClinicContext = { role: string };

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; items: ProviderSchedule[]; rooms: ClinicRoom[]; viewerRole: string };

const CONFIG_ROLES = new Set(["owner", "manager"]);
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function SchedulesSettingsPage() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [providerUserId, setProviderUserId] = useState("");
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("17:00");
  const [effectiveFrom, setEffectiveFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [roomId, setRoomId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listSchedules({ limit: 200 }), listRooms({ status: "active", limit: 200 }), apiFetch<ClinicContext>("/api/v1/clinic")])
      .then(([schedules, rooms, clinic]) =>
        setState({ kind: "loaded", items: schedules.items, rooms: rooms.items, viewerRole: clinic.role }),
      )
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
  }, []);

  useEffect(() => {
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!providerUserId.trim() || !effectiveFrom) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await createSchedule({
        provider_user_id: providerUserId.trim(),
        day_of_week: dayOfWeek,
        start_time: `${startTime}:00`,
        end_time: `${endTime}:00`,
        effective_from: effectiveFrom,
        room_id: roomId || null,
      });
      setProviderUserId("");
      load();
    } catch (error: unknown) {
      setFormError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeactivate(item: ProviderSchedule) {
    if (!window.confirm(`Deactivate this schedule rule for ${item.provider_user_id}?`)) {
      return;
    }
    setPendingId(item.id);
    setRowError(null);
    try {
      await deactivateSchedule(item.id);
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingId(null);
    }
  }

  if (state.kind === "loading") {
    return <p>Loading provider schedules…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load provider schedules: {state.message}</p>;
  }

  const canManage = CONFIG_ROLES.has(state.viewerRole);
  const roomName = (id: string | null) => (id ? state.rooms.find((r) => r.id === id)?.name ?? id : "—");

  return (
    <section>
      <h2>Provider schedules</h2>
      <p style={{ opacity: 0.8 }}>
        A &quot;provider&quot; is any staff member with an active membership - there is no separate provider
        role. Overlapping rules for the same provider/day/date-range are rejected.
      </p>
      {rowError && <p role="alert">{rowError}</p>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Provider</th>
            <th style={{ textAlign: "left" }}>Day</th>
            <th style={{ textAlign: "left" }}>Time</th>
            <th style={{ textAlign: "left" }}>Effective from</th>
            <th style={{ textAlign: "left" }}>Room</th>
            <th style={{ textAlign: "left" }}>Status</th>
            <th style={{ textAlign: "left" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((item) => (
            <tr key={item.id}>
              <td>{item.provider_user_id}</td>
              <td>{DAY_NAMES[item.day_of_week]}</td>
              <td>
                {item.start_time}–{item.end_time}
              </td>
              <td>
                {item.effective_from}
                {item.effective_until ? ` – ${item.effective_until}` : " (ongoing)"}
              </td>
              <td>{roomName(item.room_id)}</td>
              <td>{item.status}</td>
              <td>
                {canManage && item.status === "active" && (
                  <button type="button" disabled={pendingId === item.id} onClick={() => handleDeactivate(item)}>
                    Deactivate
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {canManage && (
        <>
          <h3 style={{ marginTop: "2rem" }}>Add schedule rule</h3>
          <form onSubmit={handleCreate} style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <input
              placeholder="Provider user id"
              value={providerUserId}
              onChange={(e) => setProviderUserId(e.target.value)}
              disabled={submitting}
              style={{ padding: "0.25rem", minWidth: 260 }}
            />
            <select value={dayOfWeek} onChange={(e) => setDayOfWeek(Number(e.target.value))} disabled={submitting}>
              {DAY_NAMES.map((day, index) => (
                <option key={day} value={index}>
                  {day}
                </option>
              ))}
            </select>
            <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} disabled={submitting} />
            <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} disabled={submitting} />
            <input type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} disabled={submitting} />
            <select value={roomId} onChange={(e) => setRoomId(e.target.value)} disabled={submitting}>
              <option value="">No room</option>
              {state.rooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <button type="submit" disabled={submitting || !providerUserId.trim()}>
              {submitting ? "Adding…" : "Add"}
            </button>
          </form>
          {formError && <p role="alert">{formError}</p>}
        </>
      )}
    </section>
  );
}
