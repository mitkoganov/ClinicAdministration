import { notFound } from "next/navigation";
import { isDevelopmentIdentityAvailable } from "../../lib/api";
import { DevIdentityPageClient } from "./dev-identity-page-client";

/** The reachable entry point for the retained development-identity path
 * (MED-004 repair, finding 2): unlike the banner embedded in the
 * `/settings/*` layout, this route needs no existing session at all - a
 * clean browser with nothing in localStorage yet must still be able to
 * reach it, since `getUnauthenticatedDestination()` (see app/lib/api.ts)
 * sends exactly this kind of caller here instead of to `/login`.
 *
 * Deliberately a Server Component (no "use client"): a production
 * request must get a REAL 404 - `notFound()` - not a client component
 * that renders a "Not found" string at HTTP 200. `isDevelopmentIdentityAvailable()`
 * reads `process.env.NODE_ENV`, which Next inlines at build time - this
 * check runs before anything is ever sent to the browser, so a
 * production build never ships the selector UI to this route at all;
 * localStorage and query parameters have no way to influence it. */
export default function DevIdentityPage() {
  if (!isDevelopmentIdentityAvailable()) {
    notFound();
  }

  return <DevIdentityPageClient />;
}
