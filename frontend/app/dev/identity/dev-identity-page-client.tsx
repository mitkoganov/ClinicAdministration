"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { hasConfiguredDevIdentity } from "../../lib/api";
import { IdentityBanner } from "../../settings/identity-banner";

/** The actual selector UI for the reachable development-identity entry
 * point (MED-004 repair). Split out of page.tsx so the route's
 * production-vs-development decision (see page.tsx) is a server-side
 * `notFound()` call, not a client component silently rendering a
 * "Not found" string at HTTP 200 - this component itself is never
 * reached at all in a production build, since page.tsx never renders it
 * there. Reuses `IdentityBanner` rather than re-implementing the
 * storage/selector UI - this component's only extra job is redirecting
 * once a value has actually been saved (not merely changed - clearing an
 * identity must not bounce the caller away from this page). */
export function DevIdentityPageClient() {
  const router = useRouter();

  useEffect(() => {
    function handleIdentityChanged() {
      if (hasConfiguredDevIdentity()) {
        router.push("/settings/clinic");
      }
    }
    window.addEventListener("dev-identity-changed", handleIdentityChanged);
    return () => window.removeEventListener("dev-identity-changed", handleIdentityChanged);
  }, [router]);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
      <h1>Development identity</h1>
      <p>
        Set a development user and clinic to exercise <code>/settings/*</code> without a
        production login. This never works outside a development build, and a real session
        always takes priority over it regardless.
      </p>
      <IdentityBanner />
    </main>
  );
}
