"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { hasConfiguredDevIdentity, isDevelopmentIdentityAvailable } from "../../lib/api";
import { IdentityBanner } from "../../settings/identity-banner";

/** The reachable entry point for the retained development-identity path
 * (MED-004 repair, finding 2): unlike the banner embedded in the
 * `/settings/*` layout, this route needs no existing session at all - a
 * clean browser with nothing in localStorage yet must still be able to
 * reach it, since `getUnauthenticatedDestination()` (see app/lib/api.ts)
 * sends exactly this kind of caller here instead of to `/login`. Reuses
 * `IdentityBanner` rather than re-implementing the storage/selector UI -
 * this page's only job is deciding whether to render it at all, and
 * redirecting once a value has actually been saved (not merely changed -
 * clearing an identity must not bounce the caller away from this page). */
export default function DevIdentityPage() {
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

  if (!isDevelopmentIdentityAvailable()) {
    // Direct navigation to this route in a production build (or any
    // non-development environment) must be a dead end - never the
    // selector, never a hint that this mechanism exists here.
    return (
      <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 420 }}>
        <h1>Not found</h1>
      </main>
    );
  }

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
