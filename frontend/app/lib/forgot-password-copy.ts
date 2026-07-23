// Neutral, non-enumerating copy for the forgot-password success state
// (MED-004 repair). This foundation slice has no email delivery - the
// wording here must never claim otherwise, and must stay identical
// whether or not the submitted email belongs to a real account (see
// SECURITY.md "account enumeration"). Kept as named constants (not
// inlined in the page) so a focused executable check
// (scripts/check-forgot-password-copy.ts) can assert directly on this
// text without a frontend test framework or a DOM-rendering step.

export const FORGOT_PASSWORD_SUCCESS_HEADING = "Request submitted";

export const FORGOT_PASSWORD_SUCCESS_BODY =
  "If an eligible account exists for that email address, a password reset has been requested for it. This response is the same whether or not that account exists.";

// Shown only when isDevelopmentIdentityAvailable() (see app/lib/api.ts)
// gates it - never in a production build. Deliberately does not suggest
// logging or printing the raw token (see SECURITY.md).
export const FORGOT_PASSWORD_DEVELOPMENT_NOTE =
  "Development note: this environment does not send email. Use the automated integration tests (backend/tests/integration/test_password_reset_service.py), or a development-only token retrieval hook if one has been implemented, to obtain a reset link locally.";

const FORBIDDEN_EMAIL_DELIVERY_PHRASES = [
  "has been sent",
  "email sent",
  "check your inbox",
  "check your email",
  "reset link sent",
  "we sent",
  "we've sent",
  "an email has been sent",
];

/** Used by the focused executable check to guard against this wording
 * regressing back into an email-delivery claim - never call this from
 * the page itself, the constants above are already correct by
 * construction. */
export function containsEmailDeliveryClaim(text: string): boolean {
  const lowered = text.toLowerCase();
  return FORBIDDEN_EMAIL_DELIVERY_PHRASES.some((phrase) => lowered.includes(phrase));
}
