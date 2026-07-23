"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, errorMessage } from "../../lib/api";

export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={null}>
      <AcceptInvitationForm />
    </Suspense>
  );
}

function AcceptInvitationForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [token, setToken] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // See frontend/app/reset-password/page.tsx for why this is deferred
    // via a microtask instead of called directly in the effect body.
    queueMicrotask(() => {
      const value = searchParams.get("token");
      if (value) {
        setToken(value);
        // Strip the token from the URL/browser history once captured -
        // it identifies a specific tenant/role grant and must not linger.
        router.replace("/invitations/accept");
      }
    });
  }, [searchParams, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      // The tenant and role this invitation grants come entirely from
      // the token's own server-side context - this form can never
      // submit or influence either (see app/schemas/auth.py's
      // InvitationAcceptRequest, which has no such fields at all).
      await apiFetch("/api/v1/auth/invitations/accept", {
        method: "POST",
        body: JSON.stringify({ token, display_name: displayName, password }),
      });
      // Same reasoning as frontend/app/login/page.tsx: the new session
      // this call creates starts with no clinic selected server-side.
      router.push("/select-clinic");
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Accept invitation</h1>
        <p>This invitation link is invalid or has expired.</p>
      </main>
    );
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Accept your invitation</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="invite-display-name">Your name</label>
        <br />
        <input
          id="invite-display-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <label htmlFor="invite-password">Choose a password</label>
        <br />
        <input
          id="invite-password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
          style={{ padding: "0.25rem", width: "100%", marginBottom: "0.75rem" }}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Joining…" : "Accept and join"}
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </main>
  );
}
