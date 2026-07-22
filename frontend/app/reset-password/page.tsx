"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, errorMessage } from "../lib/api";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [token, setToken] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    // Deferred via a microtask rather than called directly in the effect
    // body - see frontend/app/settings/staff/page.tsx for why (a direct
    // synchronous setState here trips react-hooks/set-state-in-effect).
    queueMicrotask(() => {
      const value = searchParams.get("token");
      if (value) {
        setToken(value);
        // Strip the token from the URL/browser history once captured in
        // component state - it must not linger in history, a bookmark,
        // or a referrer header for any later navigation.
        router.replace("/reset-password");
      }
    });
  }, [searchParams, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/api/v1/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      setSuccess(true);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (success) {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Password updated</h1>
        <p>Your password has been changed. You can now log in with your new password.</p>
      </main>
    );
  }

  if (!token) {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Reset password</h1>
        <p>This link is invalid or has expired. Request a new one from the login page.</p>
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Choose a new password</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="reset-new-password">New password</label>
        <br />
        <input
          id="reset-new-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : "Set new password"}
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </main>
  );
}
