// Focused executable check for app/lib/dev-identity-policy.ts (MED-004
// repair, finding 2). There is no frontend test framework in this
// repository - same approach as scripts/check-clinic-selection.ts: a
// small, dependency-free assertion script exercising the pure decision
// function directly, run via `npm run check:dev-identity-policy`.

import { resolveUnauthenticatedDestination } from "../app/lib/dev-identity-policy.ts";

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function testProductionAlwaysGoesToLogin(): void {
  // Production must go to /login regardless of whether a dev identity
  // happens to be configured - a value that survived into a production
  // build must never be honored.
  assertEqual(
    resolveUnauthenticatedDestination({
      developmentIdentityAvailable: false,
      configuredDevIdentity: false,
    }),
    "/login",
    "production, no dev identity -> /login",
  );
  assertEqual(
    resolveUnauthenticatedDestination({
      developmentIdentityAvailable: false,
      configuredDevIdentity: true,
    }),
    "/login",
    "production, dev identity somehow configured -> /login anyway",
  );
}

function testCleanDevelopmentBrowserGoesToDevIdentityEntry(): void {
  const destination = resolveUnauthenticatedDestination({
    developmentIdentityAvailable: true,
    configuredDevIdentity: false,
  });
  assertEqual(destination, "/dev/identity", "development, clean browser -> /dev/identity");
}

function testConfiguredDevelopmentIdentityStaysPut(): void {
  const destination = resolveUnauthenticatedDestination({
    developmentIdentityAvailable: true,
    configuredDevIdentity: true,
  });
  assertEqual(destination, null, "development, configured identity -> stay (null)");
}

const checks: Array<[string, () => void]> = [
  ["production always redirects to /login", testProductionAlwaysGoesToLogin],
  ["clean development browser -> /dev/identity", testCleanDevelopmentBrowserGoesToDevIdentityEntry],
  ["configured development identity -> stay put", testConfiguredDevelopmentIdentityStaysPut],
];

let failures = 0;
for (const [name, check] of checks) {
  try {
    check();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log(`${checks.length} check(s) passed`);
