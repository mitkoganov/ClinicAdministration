// Focused executable check for app/lib/clinic-selection.ts (MED-004
// repair, finding 1). There is no frontend test framework in this
// repository (see README.md/ARCHITECTURE.md) - this is a small,
// dependency-free assertion script exercising the pure decision function
// directly, run via `npm run check:clinic-selection`. It asserts and
// exits non-zero on the first failure, so a broken policy fails the
// script the same way a test failure would.

import { decideClinicSelection, type ClinicSummary } from "../app/lib/clinic-selection.ts";

function clinic(tenantId: string, name: string, role = "owner"): ClinicSummary {
  return { tenant_id: tenantId, name, role };
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson !== expectedJson) {
    throw new Error(`${message}: expected ${expectedJson}, got ${actualJson}`);
  }
}

function testZeroClinics(): void {
  const decision = decideClinicSelection([]);
  assertEqual(decision, { kind: "none" }, "zero clinics must decide 'none'");
}

function testSingleClinicAutoSelects(): void {
  const only = clinic("tenant-a", "Only Clinic");
  const decision = decideClinicSelection([only]);
  assertEqual(decision, { kind: "auto", clinic: only }, "one clinic must auto-select it");
}

function testMultipleClinicsRequireChoice(): void {
  const a = clinic("tenant-a", "Clinic A");
  const b = clinic("tenant-b", "Clinic B");
  const decision = decideClinicSelection([a, b]);
  assertEqual(
    decision,
    { kind: "choose", clinics: [a, b] },
    "more than one clinic must never be auto-selected",
  );
}

function testMultipleClinicsNeverPicksOneImplicitly(): void {
  const clinics = [clinic("tenant-a", "A"), clinic("tenant-b", "B"), clinic("tenant-c", "C")];
  const decision = decideClinicSelection(clinics);
  if (decision.kind !== "choose") {
    throw new Error(`three clinics must require an explicit choice, got kind=${decision.kind}`);
  }
  assertEqual(decision.clinics.length, 3, "'choose' must carry every candidate, not a subset");
}

const checks: Array<[string, () => void]> = [
  ["zero clinics -> none", testZeroClinics],
  ["single clinic -> auto", testSingleClinicAutoSelects],
  ["multiple clinics -> choose", testMultipleClinicsRequireChoice],
  ["multiple clinics never auto-picks one", testMultipleClinicsNeverPicksOneImplicitly],
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
