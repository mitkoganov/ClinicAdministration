"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, errorMessage } from "../lib/api";
import { decideClinicSelection, type ClinicSummary } from "../lib/clinic-selection";

type ClinicsResponse = { items: ClinicSummary[] };

type State =
  | { kind: "loading" }
  | { kind: "no-clinics" }
  | { kind: "choose"; clinics: ClinicSummary[] }
  | { kind: "selecting"; tenantId: string }
  | { kind: "error"; message: string };

/** The mandatory post-login step (MED-004 repair, finding 1): a brand-new
 * session always starts with no clinic selected server-side, so nothing
 * that depends on a selected clinic (`/settings/clinic`, `/settings/staff`)
 * can be reached before this page runs. `decideClinicSelection` is the
 * single place that turns "how many active clinics does this account
 * have" into a UI state - this page only renders that decision and calls
 * the backend, it never picks a clinic on the caller's behalf beyond what
 * that pure function decided. */
export default function SelectClinicPage() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    apiFetch<ClinicsResponse>("/api/v1/auth/clinics")
      .then((response) => {
        const decision = decideClinicSelection(response.items);
        if (decision.kind === "none") {
          setState({ kind: "no-clinics" });
          return;
        }
        if (decision.kind === "auto") {
          void selectAndRedirect(decision.clinic.tenant_id);
          return;
        }
        setState({ kind: "choose", clinics: decision.clinics });
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          router.replace("/login");
          return;
        }
        setState({ kind: "error", message: errorMessage(error) });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  useEffect(() => {
    // Deferred via a microtask - see frontend/app/settings/clinic/page.tsx
    // for why (a direct synchronous setState here trips
    // react-hooks/set-state-in-effect).
    queueMicrotask(load);
  }, [load]);

  async function selectAndRedirect(tenantId: string) {
    setState({ kind: "selecting", tenantId });
    try {
      await apiFetch("/api/v1/auth/select-clinic", {
        method: "POST",
        body: JSON.stringify({ tenant_id: tenantId }),
      });
      router.push("/settings/clinic");
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 401) {
        router.replace("/login");
        return;
      }
      setState({ kind: "error", message: errorMessage(error) });
    }
  }

  if (state.kind === "loading" || state.kind === "selecting") {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <p>{state.kind === "selecting" ? "Opening your clinic…" : "Loading your clinics…"}</p>
      </main>
    );
  }

  if (state.kind === "no-clinics") {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>No clinic access yet</h1>
        <p>
          Your account is signed in, but is not an active staff member of any clinic. Ask a
          clinic owner or manager to add you, then reload this page.
        </p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Could not load your clinics</h1>
        <p role="alert">{state.message}</p>
        <button type="button" onClick={load}>
          Try again
        </button>
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Choose a clinic</h1>
      <ul style={{ listStyle: "none", padding: 0 }}>
        {state.clinics.map((clinic) => (
          <li key={clinic.tenant_id} style={{ marginBottom: "0.5rem" }}>
            <button
              type="button"
              onClick={() => selectAndRedirect(clinic.tenant_id)}
              style={{ width: "100%", padding: "0.5rem", textAlign: "left" }}
            >
              {clinic.name} <span style={{ opacity: 0.7 }}>({clinic.role})</span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
