"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, errorMessage } from "../lib/api";

type MeResponse = {
  email: string;
  display_name: string;
  selected_clinic: { name: string; role: string } | null;
};

type State =
  | { kind: "loading" }
  | { kind: "unauthenticated" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; me: MeResponse };

/** Shows the current logged-in user (if any) and a logout button at the
 * top of every /settings/* page. Redirects to /login when there is no
 * valid session - the settings pages themselves never render their own
 * "please log in" state, this is the one shared place for it. */
export function SessionStatus() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    queueMicrotask(() => {
      apiFetch<MeResponse>("/api/v1/auth/me")
        .then((me) => setState({ kind: "loaded", me }))
        .catch((error: unknown) => {
          if (error instanceof ApiError && error.status === 401) {
            setState({ kind: "unauthenticated" });
            return;
          }
          setState({ kind: "error", message: errorMessage(error) });
        });
    });
  }, []);

  useEffect(() => {
    if (state.kind === "unauthenticated") {
      router.replace("/login");
    }
  }, [state.kind, router]);

  async function handleLogout() {
    const csrfToken = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/)?.[1];
    await apiFetch("/api/v1/auth/logout", {
      method: "POST",
      headers: csrfToken ? { "X-CSRF-Token": decodeURIComponent(csrfToken) } : undefined,
    }).catch(() => undefined);
    router.push("/login");
  }

  if (state.kind === "loading" || state.kind === "unauthenticated") {
    return <p>Loading session…</p>;
  }
  if (state.kind === "error") {
    return <p>Could not load session: {state.message}</p>;
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
