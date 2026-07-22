"use client";

import { useState } from "react";
import { apiFetch, errorMessage } from "../lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A single neutral "done" state regardless of whether the account
  // exists - the backend's response is identical either way, and this
  // page must never re-introduce a distinction the API deliberately
  // does not make (see SECURITY.md "account enumeration").
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/api/v1/auth/password-reset/request", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setSubmitted(true);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Check your email</h1>
        <p>
          If an account exists for that email address, a password reset link has been sent to it.
        </p>
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Forgot your password?</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="forgot-email">Email</label>
        <br />
        <input
          id="forgot-email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Sending…" : "Send reset link"}
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </main>
  );
}
