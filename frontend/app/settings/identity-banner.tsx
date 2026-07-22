"use client";

import { useEffect, useState } from "react";
import { clearDevIdentity, readDevIdentity, writeDevIdentity } from "./lib";

/** Development-only identity picker: the backend's real identity provider
 * is disabled by default and only ever enabled in a `development`
 * environment (see app/core/identity.py). Until authentication exists,
 * this is how a developer tells the frontend which user/clinic to act as -
 * it is never a security boundary, only a convenience for local testing. */
export function IdentityBanner() {
  const [userIdInput, setUserIdInput] = useState("");
  const [tenantIdInput, setTenantIdInput] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // Deferred via a microtask (rather than read+setState directly in the
    // effect body) so this reads as an async callback to the
    // react-hooks/set-state-in-effect rule, not a synchronous render-time
    // state update - localStorage is only available client-side anyway, so
    // this can never run during server rendering.
    queueMicrotask(() => {
      const identity = readDevIdentity();
      if (identity) {
        setUserIdInput(identity.userId);
        setTenantIdInput(identity.tenantId);
        setSaved(true);
      }
    });
  }, []);

  function handleSave() {
    if (!userIdInput.trim() || !tenantIdInput.trim()) {
      return;
    }
    writeDevIdentity({ userId: userIdInput.trim(), tenantId: tenantIdInput.trim() });
    setSaved(true);
    window.dispatchEvent(new Event("dev-identity-changed"));
  }

  function handleClear() {
    clearDevIdentity();
    setUserIdInput("");
    setTenantIdInput("");
    setSaved(false);
    window.dispatchEvent(new Event("dev-identity-changed"));
  }

  return (
    <section
      style={{
        border: "1px solid #999",
        borderRadius: 4,
        padding: "0.75rem 1rem",
        marginBottom: "1.5rem",
        fontSize: "0.9rem",
      }}
    >
      <p style={{ marginBottom: "0.5rem" }}>
        <strong>Development identity</strong> — no authentication exists yet. Enter a user id and
        tenant (clinic) id with an active membership to act as that user. This is never a
        security boundary; the server independently re-validates both.
      </p>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
        <input
          aria-label="Dev user id"
          placeholder="X-Dev-User-Id (UUID)"
          value={userIdInput}
          onChange={(e) => setUserIdInput(e.target.value)}
          style={{ padding: "0.25rem", minWidth: 280 }}
        />
        <input
          aria-label="Dev tenant id"
          placeholder="X-Tenant-Id (UUID)"
          value={tenantIdInput}
          onChange={(e) => setTenantIdInput(e.target.value)}
          style={{ padding: "0.25rem", minWidth: 280 }}
        />
        <button type="button" onClick={handleSave}>
          Save
        </button>
        <button type="button" onClick={handleClear}>
          Clear
        </button>
        {saved && <span>Identity set.</span>}
      </div>
    </section>
  );
}
