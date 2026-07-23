"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, errorMessage, getUnauthenticatedDestination } from "../lib/api";

type MeResponse = {
  email: string;
  display_name: string;
  selected_clinic: { name: string; role: string } | null;
};

type State =
  | { kind: "loading" }
  // A 401 from /auth/me that resolves (via getUnauthenticatedDestination)
  // to somewhere other than "stay put" - production always redirects to
  // /login; a development build with no dev identity configured yet
  // redirects to the dev-identity entry point instead, since there is no
  // session-based fallback available to it either.
  | { kind: "redirecting"; to: string }
  // A 401 from /auth/me while the explicit, build-time-gated development
  // identity path is active AND already configured (see app/lib/api.ts's
  // getUnauthenticatedDestination) is expected, not an error: settings
  // pages still work via dev headers attached by apiFetch, independent of
  // a production session. Forcing a /login redirect here would break that
  // retained local-testing path - see ARCHITECTURE.md "Dev-identity
  // isolation" and SECURITY.md "Development identity restrictions".
  | { kind: "dev-identity" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; me: MeResponse };

/** Shows the current logged-in user (if any) and a logout button at the
 * top of every /settings/* page. This is the single centralized place
 * that decides, for every settings page, whether the caller needs to be
 * sent to /login or /dev/identity (see getUnauthenticatedDestination), to
 * /select-clinic (a valid session with no clinic selected yet), or can
 * proceed - individual settings pages never re-implement any of these
 * redirects themselves. */
export function SessionStatus() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    queueMicrotask(() => {
      apiFetch<MeResponse>("/api/v1/auth/me")
        .then((me) => setState({ kind: "loaded", me }))
        .catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 401) {
            const destination = getUnauthenticatedDestination();
            setState(destination === null ? { kind: "dev-identity" } : { kind: "redirecting", to: destination });
            return;
          }
          setState({ kind: "error", message: errorMessage(error) });
        });
    });
  }, []);

  useEffect(() => {
    if (state.kind === "redirecting") {
      router.replace(state.to);
      return;
    }
    // A production session always takes priority server-side over dev
    // headers (see get_tenant_context) - this only ever fires for an
    // actual authenticated session that has not chosen a clinic yet, not
    // for the dev-identity path above, which never reaches "loaded".
    if (state.kind === "loaded" && state.me.selected_clinic === null) {
      router.replace("/select-clinic");
    }
  }, [state, router]);

  async function handleLogout() {
    const csrfToken = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/)?.[1];
    await apiFetch("/api/v1/auth/logout", {
      method: "POST",
      headers: csrfToken ? { "X-CSRF-Token": decodeURIComponent(csrfToken) } : undefined,
    }).catch(() => undefined);
    router.push("/login");
  }

  if (state.kind === "loading" || state.kind === "redirecting") {
    return <p>Loading session…</p>;
  }
  if (state.kind === "dev-identity") {
    return (
      <p style={{ fontSize: "0.9rem", opacity: 0.8 }}>
        Development identity active — using the X-Dev-User-Id/X-Tenant-Id headers below instead
        of a production session.
      </p>
    );
  }
  if (state.kind === "error") {
    return <p>Could not load session: {state.message}</p>;
  }
  if (state.me.selected_clinic === null) {
    // Redirecting to /select-clinic - see the effect above.
    return <p>Loading session…</p>;
  }

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: "1rem",
        paddingBottom: "0.5rem",
        borderBottom: "1px solid #ccc",
      }}
    >
      <span>
        {state.me.display_name} ({state.me.email})
        {state.me.selected_clinic && (
          <> — {state.me.selected_clinic.name} ({state.me.selected_clinic.role})</>
        )}
      </span>
      <button type="button" onClick={handleLogout}>
        Log out
      </button>
    </div>
  );
}
