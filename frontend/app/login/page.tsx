"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      router.push("/settings/clinic");
    } catch (err: unknown) {
      // The backend already returns a single generic message for every
      // failure shape (wrong password, unknown account, inactive
      // account, rate-limited) - this page never re-interprets it.
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Log in</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="login-email">Email</label>
        <br />
        <input
          id="login-email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <label htmlFor="login-password">Password</label>
        <br />
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Logging in…" : "Log in"}
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
      <p style={{ marginTop: "1rem" }}>
        <Link href="/forgot-password">Forgot your password?</Link>
      </p>
    </main>
  );
}
