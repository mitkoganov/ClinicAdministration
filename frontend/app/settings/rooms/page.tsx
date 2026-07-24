"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "../../lib/api";
import { createRoom, deactivateRoom, listRooms, updateRoom, type ClinicRoom } from "../../lib/calendar-api";

type ClinicContext = { role: string };

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; rooms: ClinicRoom[]; viewerRole: string };

const CONFIG_ROLES = new Set(["owner", "manager"]);

export default function RoomsSettingsPage() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listRooms({ limit: 200 }), apiFetch<ClinicContext>("/api/v1/clinic")])
      .then(([rooms, clinic]) => setState({ kind: "loaded", rooms: rooms.items, viewerRole: clinic.role }))
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
  }, []);

  useEffect(() => {
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !code.trim()) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await createRoom({ name: name.trim(), code: code.trim(), description: description.trim() || null });
      setName("");
      setCode("");
      setDescription("");
      load();
    } catch (error: unknown) {
      setFormError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeactivate(room: ClinicRoom) {
    if (!window.confirm(`Deactivate room "${room.name}"?`)) {
      return;
    }
    setPendingId(room.id);
    setRowError(null);
    try {
      await deactivateRoom(room.id);
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingId(null);
    }
  }

  async function handleRename(room: ClinicRoom) {
    const newName = window.prompt("New room name:", room.name);
    if (!newName || !newName.trim() || newName.trim() === room.name) {
      return;
    }
    setPendingId(room.id);
    setRowError(null);
    try {
      await updateRoom(room.id, { name: newName.trim() });
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingId(null);
    }
  }

  if (state.kind === "loading") {
    return <p>Loading rooms…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load rooms: {state.message}</p>;
  }

  const canManage = CONFIG_ROLES.has(state.viewerRole);

  return (
    <section>
      <h2>Rooms</h2>
      {rowError && <p role="alert">{rowError}</p>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Name</th>
            <th style={{ textAlign: "left" }}>Code</th>
            <th style={{ textAlign: "left" }}>Description</th>
            <th style={{ textAlign: "left" }}>Status</th>
            <th style={{ textAlign: "left" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {state.rooms.map((room) => (
            <tr key={room.id}>
              <td>{room.name}</td>
              <td>{room.code}</td>
              <td>{room.description ?? "—"}</td>
              <td>{room.status}</td>
              <td style={{ display: "flex", gap: "0.5rem" }}>
                {canManage && (
                  <>
                    <button type="button" disabled={pendingId === room.id} onClick={() => handleRename(room)}>
                      Rename
                    </button>
                    {room.status === "active" && (
                      <button type="button" disabled={pendingId === room.id} onClick={() => handleDeactivate(room)}>
                        Deactivate
                      </button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {canManage && (
        <>
          <h3 style={{ marginTop: "2rem" }}>Add room</h3>
          <form onSubmit={handleCreate} style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} disabled={submitting} style={{ padding: "0.25rem" }} />
            <input placeholder="Code" value={code} onChange={(e) => setCode(e.target.value)} disabled={submitting} style={{ padding: "0.25rem" }} />
            <input placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} disabled={submitting} style={{ padding: "0.25rem", minWidth: 220 }} />
            <button type="submit" disabled={submitting || !name.trim() || !code.trim()}>
              {submitting ? "Adding…" : "Add"}
            </button>
          </form>
          {formError && <p role="alert">{formError}</p>}
        </>
      )}
    </section>
  );
}
