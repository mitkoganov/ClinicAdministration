"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "../../lib/api";
import {
  createServiceType,
  deactivateServiceType,
  listServiceTypes,
  type AppointmentServiceType,
} from "../../lib/calendar-api";

type ClinicContext = { role: string };

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; items: AppointmentServiceType[]; viewerRole: string };

const CONFIG_ROLES = new Set(["owner", "manager"]);

export default function ServiceTypesSettingsPage() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [duration, setDuration] = useState(30);
  const [bufferBefore, setBufferBefore] = useState(0);
  const [bufferAfter, setBufferAfter] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listServiceTypes({ limit: 200 }), apiFetch<ClinicContext>("/api/v1/clinic")])
      .then(([list, clinic]) => setState({ kind: "loaded", items: list.items, viewerRole: clinic.role }))
      .catch((error: unknown) => setState({ kind: "error", message: errorMessage(error) }));
  }, []);

  useEffect(() => {
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !code.trim() || duration <= 0) {
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await createServiceType({
        name: name.trim(),
        code: code.trim(),
        description: null,
        default_duration_minutes: duration,
        buffer_before_minutes: bufferBefore,
        buffer_after_minutes: bufferAfter,
      });
      setName("");
      setCode("");
      setDuration(30);
      setBufferBefore(0);
      setBufferAfter(0);
      load();
    } catch (error: unknown) {
      setFormError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeactivate(item: AppointmentServiceType) {
    if (!window.confirm(`Deactivate service type "${item.name}"?`)) {
      return;
    }
    setPendingId(item.id);
    setRowError(null);
    try {
      await deactivateServiceType(item.id);
      load();
    } catch (error: unknown) {
      setRowError(errorMessage(error));
    } finally {
      setPendingId(null);
    }
  }

  if (state.kind === "loading") {
    return <p>Loading service types…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load service types: {state.message}</p>;
  }

  const canManage = CONFIG_ROLES.has(state.viewerRole);

  return (
    <section>
      <h2>Service types</h2>
      {rowError && <p role="alert">{rowError}</p>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Name</th>
            <th style={{ textAlign: "left" }}>Code</th>
            <th style={{ textAlign: "left" }}>Duration (min)</th>
            <th style={{ textAlign: "left" }}>Buffer before/after</th>
            <th style={{ textAlign: "left" }}>Status</th>
            <th style={{ textAlign: "left" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((item) => (
            <tr key={item.id}>
              <td>{item.name}</td>
              <td>{item.code}</td>
              <td>{item.default_duration_minutes}</td>
              <td>
                {item.buffer_before_minutes} / {item.buffer_after_minutes}
              </td>
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
          <h3 style={{ marginTop: "2rem" }}>Add service type</h3>
          <form onSubmit={handleCreate} style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} disabled={submitting} style={{ padding: "0.25rem" }} />
            <input placeholder="Code" value={code} onChange={(e) => setCode(e.target.value)} disabled={submitting} style={{ padding: "0.25rem" }} />
            <label>
              Duration (min){" "}
              <input type="number" min={1} value={duration} onChange={(e) => setDuration(Number(e.target.value))} disabled={submitting} style={{ width: 80 }} />
            </label>
            <label>
              Buffer before{" "}
              <input type="number" min={0} value={bufferBefore} onChange={(e) => setBufferBefore(Number(e.target.value))} disabled={submitting} style={{ width: 70 }} />
            </label>
            <label>
              Buffer after{" "}
              <input type="number" min={0} value={bufferAfter} onChange={(e) => setBufferAfter(Number(e.target.value))} disabled={submitting} style={{ width: 70 }} />
            </label>
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
