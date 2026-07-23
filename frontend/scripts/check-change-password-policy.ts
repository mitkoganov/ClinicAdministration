// Focused executable check for app/lib/change-password-policy.ts
// (MED-004 repair). No frontend test framework in this repository - same
// approach as scripts/check-clinic-selection.ts and
// scripts/check-dev-identity-policy.ts.

import {
  validateChangePasswordForm,
  type ChangePasswordFormValues,
} from "../app/lib/change-password-policy.ts";

function values(overrides: Partial<ChangePasswordFormValues>): ChangePasswordFormValues {
  return {
    currentPassword: "the current passphrase!",
    newPassword: "a brand new passphrase!!",
    confirmNewPassword: "a brand new passphrase!!",
    ...overrides,
  };
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function testEmptyCurrentPassword(): void {
  const error = validateChangePasswordForm(values({ currentPassword: "" }));
  assertEqual(error, "current-password-required", "empty current password");
}

function testShortNewPassword(): void {
  const error = validateChangePasswordForm(values({ newPassword: "short", confirmNewPassword: "short" }));
  assertEqual(error, "new-password-too-short", "short new password");
}

function testWhitespaceOnlyNewPassword(): void {
  const whitespace = "            ";
  const error = validateChangePasswordForm(
    values({ newPassword: whitespace, confirmNewPassword: whitespace }),
  );
  assertEqual(error, "new-password-whitespace-only", "whitespace-only new password");
}

function testTooLongNewPassword(): void {
  const tooLong = "a".repeat(300);
  const error = validateChangePasswordForm(values({ newPassword: tooLong, confirmNewPassword: tooLong }));
  assertEqual(error, "new-password-too-long", "too-long new password");
}

function testMismatchedConfirmation(): void {
  const error = validateChangePasswordForm(
    values({ newPassword: "a brand new passphrase!!", confirmNewPassword: "a different one entirely!!" }),
  );
  assertEqual(error, "confirmation-mismatch", "mismatched confirmation");
}

function testValidForm(): void {
  const error = validateChangePasswordForm(values({}));
  assertEqual(error, null, "valid form");
}

const checks: Array<[string, () => void]> = [
  ["empty current password -> current-password-required", testEmptyCurrentPassword],
  ["short new password -> new-password-too-short", testShortNewPassword],
  ["whitespace-only new password -> new-password-whitespace-only", testWhitespaceOnlyNewPassword],
  ["too-long new password -> new-password-too-long", testTooLongNewPassword],
  ["mismatched confirmation -> confirmation-mismatch", testMismatchedConfirmation],
  ["valid form -> null", testValidForm],
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
