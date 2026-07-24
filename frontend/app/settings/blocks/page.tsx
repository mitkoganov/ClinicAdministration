"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "../../lib/api";
import { formatTimeRange, localDayBoundsUtc, localWallClockToUtc, todayInTimezone } from "../../lib/calendar-time";
import {
  createBlock,
  deleteBlock,
  listBlocks,
  listRooms,
  type CalendarBlock,
  type CalendarBlockType,
  type ClinicRoom,
} from "../../lib/calendar-api";

type Clinic = { role: string; timezone: string };

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; blocks: CalendarBlock[]; rooms: ClinicRoom[]; clinic: Clinic };

const CONFIG_ROLES = new Set(["owner", "manager"]);
const BLOCK_TYPES: CalendarBlockType[] = [
  "leave",
  "training",
  "maintenance",
  "room_closure",
  "personal",
  "other",
];

export default function BlocksSettingsPage() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [rangeStart, setRangeStart] = useState<string | null>(null);
  const [rangeDays, setRangeDays] = useState(7);
  const [providerUserId, setProviderUserId] = useState("");
  const [roomId, setRoomId] = useState("");
  const [date, setDate] = useState("");
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("10:00");
  const [reason, setReason] = useState("");
  const [blockType, setBlockType] = useState<CalendarBlockType>("other");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch<Clinic>("/api/v1/clinic")
      .then((clinic) => {
        const start = rangeStart ?? todayInTimezone(clinic.timezone);
        const { start: rangeStartUtc } = localDayBoundsUtc(start, clinic.timezone);
        const endDate = new Date(rangeStartUtc.getTime() + rangeDays * 24 * 60 * 60 * 1000).toISOString();
        return Promise.all([
          listBlocks({ date_from: rangeStartUtc.toISOString(), date_to: endDate }),
          listRooms({ status: "active", limit: 200 }),
        ]).then(([blocks, rooms]) => {
          setState({ kind: "loaded", blocks, rooms: rooms.items, clinic });
          setRangeStart((current) => current ?? start);
          setDate((current) => current || start);
        });
      })
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeDays]);

  useEffect(() => {
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (state.kind !== "loaded" || (!providerUserId.trim() && !roomId) || !date || !reason.trim()) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const startsAt = localWallClockToUtc(date, startTime, state.clinic.timezone).toISOString();
      const endsAt = localWallClockToUtc(date, endTime, state.clinic.timezone).toISOString();
      await createBlock({
        provider_user_id: providerUserId.trim() || null,
        room_id: roomId || null,
        starts_at: startsAt,
        ends_at: endsAt,
        reason: reason.trim(),
        block_type: blockType,
      });
      setReason("");
      load();
    } catch (error: unknown) {
      setFormError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(block: CalendarBlock) {
    if (!window.confirm(`Remove this ${block.block_type} block ("${block.reason}")?`)) {
      return;
    }
    setPendingId(block.id);
    setRowError(null);
    try {
      await deleteBlock(block.id);
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingId(null);
    }
  }

  if (state.kind === "loading") {
    return <p>Loading calendar blocks…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load calendar blocks: {state.message}</p>;
  }

  const canManage = CONFIG_ROLES.has(state.clinic.role);
  const roomName = (id: string | null) => (id ? state.rooms.find((r) => r.id === id)?.name ?? id : "—");

  return (
    <section>
      <h2>Calendar blocks</h2>
      <p style={{ opacity: 0.8 }}>
        Showing blocks for the next {rangeDays} day(s) from {rangeStart}, in the clinic&apos;s own timezone (
        {state.clinic.timezone}).{" "}
        <button type="button" onClick={() => setRangeDays((d) => Math.min(31, d + 7))}>
          Show more
        </button>
      </p>
      {rowError && <p role="alert">{rowError}</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {state.blocks.length === 0 && <li>No blocked periods in this range.</li>}
        {state.blocks.map((block) => (
          <li key={block.id} style={{ border: "1px solid #ccc", padding: "0.5rem", borderRadius: 4, marginBottom: "0.5rem" }}>
            {formatTimeRange(block.starts_at, block.ends_at, state.clinic.timezone)} — {block.block_type}: {block.reason}
            {block.provider_user_id && <> · Provider: {block.provider_user_id}</>}
            {block.room_id && <> · Room: {roomName(block.room_id)}</>}
            {canManage && (
              <button type="button" style={{ marginLeft: "1rem" }} disabled={pendingId === block.id} onClick={() => handleDelete(block)}>
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>

      {canManage && (
        <>
          <h3 style={{ marginTop: "2rem" }}>Add block</h3>
          <form onSubmit={handleCreate} style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <input
              placeholder="Provider user id (optional)"
              value={providerUserId}
              onChange={(e) => setProviderUserId(e.target.value)}
              disabled={submitting}
              style={{ padding: "0.25rem", minWidth: 240 }}
            />
            <select value={roomId} onChange={(e) => setRoomId(e.target.value)} disabled={submitting}>
              <option value="">No room</option>
              {state.rooms.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} disabled={submitting} />
            <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} disabled={submitting} />
            <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} disabled={submitting} />
            <select value={blockType} onChange={(e) => setBlockType(e.target.value as CalendarBlockType)} disabled={submitting}>
              {BLOCK_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
            <input
              placeholder="Reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              style={{ padding: "0.25rem", minWidth: 200 }}
            />
            <button type="submit" disabled={submitting || (!providerUserId.trim() && !roomId) || !reason.trim()}>
              {submitting ? "Adding…" : "Add"}
            </button>
          </form>
          {formError && <p role="alert">{formError}</p>}
        </>
      )}
    </section>
  );
}
