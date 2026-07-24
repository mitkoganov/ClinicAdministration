// Focused executable check for app/lib/forgot-password-copy.ts
// (MED-004 repair). No frontend test framework in this repository - same
// approach as scripts/check-clinic-selection.ts and friends.

import {
  containsEmailDeliveryClaim,
  FORGOT_PASSWORD_DEVELOPMENT_NOTE,
  FORGOT_PASSWORD_SUCCESS_BODY,
  FORGOT_PASSWORD_SUCCESS_HEADING,
} from "../app/lib/forgot-password-copy.ts";

function assertTrue(condition: boolean, message: string): void {
  if (!condition) {
    throw new Error(message);
  }
}

function testProductionTextMakesNoEmailDeliveryClaim(): void {
  assertTrue(
    !containsEmailDeliveryClaim(FORGOT_PASSWORD_SUCCESS_HEADING),
    "success heading must not claim email delivery",
  );
  assertTrue(
    !containsEmailDeliveryClaim(FORGOT_PASSWORD_SUCCESS_BODY),
    "success body must not claim email delivery",
  );
}

function testProductionTextIsNonEnumerating(): void {
  const lowered = FORGOT_PASSWORD_SUCCESS_BODY.toLowerCase();
  assertTrue(
    lowered.includes("if an eligible account exists"),
    "success body must hedge with a conditional, never assert the account exists",
  );
}

function testDevelopmentNoteDoesNotSuggestLoggingTheToken(): void {
  const lowered = FORGOT_PASSWORD_DEVELOPMENT_NOTE.toLowerCase();
  for (const forbidden of ["log the", "print the", "debug log", "console.log"]) {
    assertTrue(
      !lowered.includes(forbidden),
      `development note must not suggest '${forbidden}' for the raw token`,
    );
  }
}

function testDetectorCatchesKnownEmailDeliveryPhrases(): void {
  const knownBadPhrases = [
    "A reset link has been sent to your email.",
    "Email sent successfully.",
    "Please check your inbox.",
    "check your email for the reset link",
    "Your reset link sent.",
  ];
  for (const phrase of knownBadPhrases) {
    assertTrue(containsEmailDeliveryClaim(phrase), `detector missed: "${phrase}"`);
  }
}

function testDetectorAllowsNeutralWording(): void {
  assertTrue(
    !containsEmailDeliveryClaim(FORGOT_PASSWORD_SUCCESS_BODY),
    "detector must not flag the actual neutral copy as a false positive",
  );
}

const checks: Array<[string, () => void]> = [
  ["production text makes no email-delivery claim", testProductionTextMakesNoEmailDeliveryClaim],
  ["production text is non-enumerating", testProductionTextIsNonEnumerating],
  ["development note never suggests logging the token", testDevelopmentNoteDoesNotSuggestLoggingTheToken],
  ["detector catches known email-delivery phrases", testDetectorCatchesKnownEmailDeliveryPhrases],
  ["detector allows the actual neutral wording", testDetectorAllowsNeutralWording],
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
