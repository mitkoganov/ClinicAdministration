"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage, readDevIdentity } from "../lib";

type Clinic = {
  id: string;
  name: string;
  slug: string;
  status: "active" | "inactive";
  role: string;
};

type ClinicState =
  | { kind: "no-identity" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; clinic: Clinic };

const WRITE_ROLES = new Set(["owner"]);

export default function ClinicSettingsPage() {
  const [state, setState] = useState<ClinicState>({ kind: "loading" });
  const [nameInput, setNameInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  const load = useCallback(() => {
    const identity = readDevIdentity();
    if (!identity) {
      setState({ kind: "no-identity" });
      return;
    }
    setState({ kind: "loading" });
    apiFetch<Clinic>(identity, "/api/v1/clinic")
      .then((clinic) => {
        setState({ kind: "loaded", clinic });
        setNameInput(clinic.name);
      })
      .catch((error: unknown) => {
        setState({ kind: "error", message: errorMessage(error) });
      });
  }, []);

  useEffect(() => {
    // Deferred via a microtask rather than called directly: `load` itself
    // calls setState synchronously before its first `await`, and doing that
    // straight from an effect body trips react-hooks/set-state-in-effect.
    queueMicrotask(load);
    window.addEventListener("dev-identity-changed", load);
    return () => window.removeEventListener("dev-identity-changed", load);
  }, [load]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const identity = readDevIdentity();
    if (!identity || state.kind !== "loaded") {
      return;
    }
    if (!nameInput.trim()) {
      setSubmitError("Clinic name cannot be empty.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(false);
    try {
      const updated = await apiFetch<Clinic>(identity, "/api/v1/clinic", {
        method: "PATCH",
        body: JSON.stringify({ name: nameInput.trim() }),
      });
      setState({ kind: "loaded", clinic: updated });
      setSubmitSuccess(true);
    } catch (error: unknown) {
      setSubmitError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  if (state.kind === "no-identity") {
    return <p>Set a development identity above to view clinic settings.</p>;
  }
  if (state.kind === "loading") {
    return <p>Loading clinic settings…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load clinic settings: {state.message}</p>;
  }

  const canEdit = WRITE_ROLES.has(state.clinic.role);

  return (
    <section>
      <h2>Clinic settings</h2>
      <dl>
        <dt>Slug</dt>
        <dd>{state.clinic.slug}</dd>
        <dt>Status</dt>
        <dd>{state.clinic.status}</dd>
        <dt>Your role</dt>
        <dd>{state.clinic.role}</dd>
      </dl>

      <form onSubmit={handleSubmit} style={{ marginTop: "1rem" }}>
        <label htmlFor="clinic-name">Clinic name</label>
        <br />
        <input
          id="clinic-name"
          value={nameInput}
          onChange={(e) => setNameInput(e.target.value)}
          disabled={!canEdit || submitting}
          style={{ padding: "0.25rem", minWidth: 300 }}
        />
        <br />
        <button type="submit" disabled={!canEdit || submitting} style={{ marginTop: "0.5rem" }}>
          {submitting ? "Saving…" : "Save"}
        </button>
        {!canEdit && <p>Your role ({state.clinic.role}) cannot edit clinic settings.</p>}
        {submitError && <p role="alert">{submitError}</p>}
        {submitSuccess && <p>Saved.</p>}
      </form>
    </section>
  );
}
