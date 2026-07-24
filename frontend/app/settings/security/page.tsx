"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, errorMessage } from "../../lib/api";
import {
  changePasswordValidationMessage,
  validateChangePasswordForm,
} from "../../lib/change-password-policy";

type State =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success" }
  | { kind: "error"; message: string };

/** Authenticated change-password form (MED-004 repair). Reached only
 * from inside the /settings/* shell, so a valid session already exists
 * by the time this renders - see app/settings/session-status.tsx for
 * the shared unauthenticated redirect. `POST /api/v1/auth/change-password`
 * keeps the caller's own session active and revokes every OTHER session
 * for the account (see app.services.auth_service.AuthService.
 * change_password) - it never returns new cookies, so this page confirms
 * the session is still usable via GET /api/v1/auth/me after a success
 * response, rather than assuming what the backend does. */
export default function ChangePasswordPage() {
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const validationError = validateChangePasswordForm({
    currentPassword,
    newPassword,
    confirmNewPassword,
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (validationError) {
      setState({ kind: "error", message: changePasswordValidationMessage(validationError) });
      return;
    }

    setState({ kind: "submitting" });
    try {
      await apiFetch("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      // The backend never returns new cookies here - confirm the current
      // session is still usable rather than assuming it is.
      try {
        await apiFetch("/api/v1/auth/me");
        setState({ kind: "success" });
        setCurrentPassword("");
        setNewPassword("");
        setConfirmNewPassword("");
      } catch (meError: unknown) {
        if (meError instanceof ApiError && meError.status === 401) {
          router.replace("/login");
          return;
        }
        setState({ kind: "success" });
      }
    } catch (error: unknown) {
      // The backend already returns a single generic message for both
      // "wrong current password" and any policy violation it independently
      // re-checks - this page never re-interprets it.
      setState({ kind: "error", message: errorMessage(error) });
    }
  }

  const submitting = state.kind === "submitting";

  return (
    <section>
      <h2>Change password</h2>
      <form onSubmit={handleSubmit}>
        <label htmlFor="current-password">Current password</label>
        <br />
        <input
          id="current-password"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <label htmlFor="new-password">New password</label>
        <br />
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <label htmlFor="confirm-new-password">Confirm new password</label>
        <br />
        <input
          id="confirm-new-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={confirmNewPassword}
          onChange={(e) => setConfirmNewPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Changing…" : "Change password"}
        </button>
        {state.kind === "error" && <p role="alert">{state.message}</p>}
        {state.kind === "success" && <p>Your password has been changed.</p>}
      </form>
    </section>
  );
}
