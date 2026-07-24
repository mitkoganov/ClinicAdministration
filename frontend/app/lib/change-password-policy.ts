// Pure client-side validation for the authenticated change-password form
// (MED-004 repair). Mirrors app.core.passwords.validate_password_policy
// exactly (same order, same thresholds) so a user gets the same UX
// feedback the backend would ultimately enforce anyway - the backend
// remains authoritative and independently re-validates every field; this
// module exists only to avoid a pointless round trip for an input the
// server would reject anyway, never to relax or replace that check.
//
// No I/O, no fetch, no DOM - kept dependency-free so it can be exercised
// by a focused executable check (scripts/check-change-password-policy.ts),
// the same pattern as app/lib/clinic-selection.ts and
// app/lib/dev-identity-policy.ts. There is no frontend test framework in
// this repository.

export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 256;

export type ChangePasswordFormValues = {
  currentPassword: string;
  newPassword: string;
  confirmNewPassword: string;
};

export type ChangePasswordValidationError =
  | "current-password-required"
  | "new-password-whitespace-only"
  | "new-password-too-short"
  | "new-password-too-long"
  | "confirmation-mismatch";

/** Returns the first policy violation found, or `null` if the form is
 * valid enough to submit. Order matches the backend's own check order
 * exactly (see app.core.passwords.validate_password_policy). */
export function validateChangePasswordForm(
  values: ChangePasswordFormValues,
): ChangePasswordValidationError | null {
  if (values.currentPassword.length === 0) {
    return "current-password-required";
  }
  if (values.newPassword.trim().length === 0) {
    return "new-password-whitespace-only";
  }
  if (values.newPassword.length < PASSWORD_MIN_LENGTH) {
    return "new-password-too-short";
  }
  if (values.newPassword.length > PASSWORD_MAX_LENGTH) {
    return "new-password-too-long";
  }
  if (values.newPassword !== values.confirmNewPassword) {
    return "confirmation-mismatch";
  }
  return null;
}

export function changePasswordValidationMessage(error: ChangePasswordValidationError): string {
  switch (error) {
    case "current-password-required":
      return "Enter your current password.";
    case "new-password-whitespace-only":
      return "New password must not be empty or whitespace-only.";
    case "new-password-too-short":
      return `New password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
    case "new-password-too-long":
      return `New password must be at most ${PASSWORD_MAX_LENGTH} characters.`;
    case "confirmation-mismatch":
      return "New password and confirmation do not match.";
    default: {
      const exhaustiveCheck: never = error;
      return exhaustiveCheck;
    }
  }
}
