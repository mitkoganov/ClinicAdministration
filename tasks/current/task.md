# MED-004 — Production Authentication and User Identity Foundation

## Preconditions

This task builds on the approved MED-002 (multi-tenant foundation) and
MED-003 (clinic and staff administration) work, merged into `master`.
Implementation happens on `feature/med-004-production-authentication`.

## Objective

Implement production-grade authentication and user identity for the
existing multi-tenant clinic administration platform.

The functional vertical slice includes:

* user account persistence;
* secure password authentication;
* login;
* logout;
* current-session identity;
* server-side session revocation;
* user-to-tenant-membership resolution;
* clinic selection when a user has more than one active membership;
* a foundation for password-reset tokens;
* a foundation for staff-invitation acceptance;
* security audit events;
* frontend login and session UX;
* strict separation between production authentication and the existing
  development identity mechanism.

## Out of scope

Patient records, appointments, billing, clinical workflows, production
email delivery, social login, SAML, OIDC, and MFA are explicitly not part
of this task. Extension points for future MFA and external identity
providers should be considered in the design but not implemented.

---

## Authentication architecture

Server-side sessions, not stateless JWT access tokens, are the browser
authentication model:

* an opaque, cryptographically random session token;
* a session record persisted server-side;
* only a cryptographic hash of the session token is stored in the
  database — never the raw token;
* the raw token is transmitted only via a secure cookie;
* the cookie is `HttpOnly`;
* the cookie uses `SameSite=Lax` or stricter;
* the cookie is `Secure` outside local development;
* the cookie has an appropriate `Path`;
* the session token is never written to `localStorage`;
* the session token is never returned in a plain JSON response;
* logout revokes the server-side session and clears the cookie.

The backend determines the authenticated user only from a validated
server-side session. Tenant and role are still resolved server-side from
live membership data on every request — no authoritative role or
permission claim is ever stored in a browser token or a long-lived
session snapshot.

## Password security

Argon2id via an established, audited library (never a hand-rolled hash).

* library-managed unique salt per password;
* configurable cost parameters;
* safe password verification;
* rehash detection when parameters change;
* passwords are never logged;
* password hashes are never returned by any API;
* password hash is excluded from repr/debug output where practical;
* no silent truncation;
* a maximum input length guards against hashing-cost DoS.

Password policy (documented here as the single source of truth):

* minimum 12 characters;
* a generous maximum length (guards against DoS, not usability);
* empty and whitespace-only passwords are rejected;
* long passphrases and password-manager-generated passwords are
  accepted;
* no mandatory mix of character classes.

## Data model

### UserAccount

* `id`;
* normalized email (case-insensitive unique identifier);
* display name;
* password hash;
* status (active/inactive);
* email verification state (for a future flow — not implemented here);
* `created_at`, `updated_at`, `password_changed_at`,
  `last_successful_login_at`;
* minimal failed-login metadata needed for throttling.

No plaintext password is ever persisted. Email normalization is
deterministic (lowercase + trim). Case-insensitive uniqueness is enforced
at the database level using the smallest reliable mechanism compatible
with PostgreSQL and this project's existing conventions (a unique index
on the already-normalized column).

### AuthSession

* `id`, `user_id`, `session_token_hash` (unique);
* `selected_tenant_id` (nullable — server-side clinic selection);
* `created_at`, `last_seen_at`, `absolute_expires_at`, `idle_expires_at`;
* `revoked_at`, a safe revocation reason.

The raw session token is never persisted.

### One-time tokens

A purpose-bound token foundation shared by password reset and staff
invitation acceptance:

* cryptographically strong raw token, only its hash stored;
* explicit `purpose`;
* single-use (`consumed_at`);
* `expires_at`, `revoked_at`;
* binding to the correct user/invitation context;
* the raw token is never logged.

Email delivery is not implemented. A password-reset request never
reveals whether the account exists.

## Account and membership relationship

The existing tenant membership model is unchanged. A user may have zero,
one, or several active clinic memberships. Authentication proves
identity; authorization still requires an active tenant and an active
membership on every tenant-scoped request.

* login can succeed with no active clinic membership;
* tenant-scoped routes require a validly selected clinic;
* an inactive account cannot authenticate;
* an inactive membership cannot authorize;
* an inactive tenant cannot be selected;
* role changes apply on the very next request (never cached in the
  session);
* the session never stores an authoritative role.

Clinic selection model: the session identifies the user; the selected
tenant is stored server-side in session metadata; `select-clinic`
validates membership; every tenant-scoped request re-validates tenant and
membership from the database; the client can never supply or influence a
role.

## API endpoints

```
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/auth/clinics
POST /api/v1/auth/select-clinic
POST /api/v1/auth/change-password
POST /api/v1/auth/password-reset/request
POST /api/v1/auth/password-reset/confirm
POST /api/v1/auth/invitations/accept
```

* **Login** — normalized email/password in, generic authentication
  failure out (never reveals whether the account exists; an inactive
  account gets the identical generic failure); verifies the password
  safely, rehashes on outdated parameters, creates a new session
  (preventing session fixation), sets the secure cookie. A failed login
  never creates a session. Audit events never contain secrets.
* **Logout** — revokes the current session, clears the cookie, may be
  idempotent, never reveals the token.
* **GET /auth/me** — returns only: user id, a safe email/identity label,
  display name, the selected clinic (if still valid), the freshly
  resolved membership role, and safe session-expiration metadata. Never
  returns a password hash, session hash, reset/invitation token hash, or
  internal counters.
* **Clinic list/selection** — lists only active memberships in active
  tenants; selection is server-side validated; an inactive tenant or
  membership is rejected; cross-tenant selection is impossible; role and
  status are never accepted from the client.
* **Change password** — requires the current password, validates the new
  password against policy, updates the hash and
  `password_changed_at`, revokes other sessions, and makes an explicit,
  documented decision about whether the current session is kept
  (rotated) or also revoked; audits safely.
* **Password reset request** — identical outward response whether or not
  the account exists; creates an expiring, purpose-bound token for a
  valid account; the raw token is never returned by the production API (a
  development-only hook may exist behind strict, explicit gating).
* **Password reset confirm** — validates token hash, purpose, expiry,
  consumed/revoked state, and the associated user; on success updates the
  password, consumes the token, revokes all sessions, and audits safely.
* **Invitation acceptance** — tenant, role, inviter, and membership come
  from the invitation token's own context; the client can never choose
  them. Does not claim production email delivery exists.

## Session lifecycle

Documented here: absolute lifetime, idle lifetime, cookie lifetime,
last-seen refresh interval, revocation behavior, expired-session cleanup
strategy, and (if needed) a maximum-active-sessions bound. Avoid a
database write on every single request (e.g. throttle `last_seen_at`
refresh). Session validation fails closed on: missing cookie, malformed
token, unknown token, revoked session, absolute expiry, idle expiry, or
an inactive account. Session tokens are hashed with a fast cryptographic
hash suitable for high-entropy random input (not the password hasher).
Raw tokens are never logged.

## CSRF protection

Because authentication is cookie-based, CSRF protection is required for
every authenticated, mutating browser request (a synchronizer-token or
double-submit pattern). Login CSRF is considered; logout is protected or
deliberately safely designed; GET requests have no side effects;
production cannot disable CSRF via a development shortcut; the frontend
fetch helper attaches the CSRF token automatically. `SameSite` alone is
not relied upon.

## Brute-force and abuse protection

A minimal, reliable login-throttling defense, Redis-backed (Redis is
already part of the stack) if practical. Rate-limited by normalized
account identifier and a safe network key. Requirements: a generic
response that never reveals account existence; a bounded backoff/lockout
(never a permanent denial of service); success resets or reduces
counters; a clearly defined fail-behavior if Redis is unavailable; no
passwords or tokens in logs; deterministic tests using a fake clock/store.
Not a general-purpose distributed security platform.

## Development identity isolation

The existing development identity mechanism (`X-Dev-User-Id`/
`X-Tenant-Id`) stays disabled by default, impossible in production
(fail-closed startup validation), and can never override a production
session. The frontend dev-identity selector is hidden in production. The
MED-003 `/settings/*` pages move to using the authenticated session by
default; the dev-header path remains only for tests that specifically
exercise development-identity behavior, and existing tests are migrated
safely rather than the mechanism being removed outright.

## Backend architecture

`API → service → repository → database`, matching the existing style:

* `app/api/auth.py`
* `app/services/auth_service.py`, `app/services/session_service.py`,
  `app/services/password_reset_service.py`
* `app/repositories/user_account.py`, `app/repositories/auth_session.py`
* `app/core/passwords.py`, `app/core/session_tokens.py`,
  `app/core/csrf.py`, `app/core/rate_limit.py`
* an authentication dependency for FastAPI routes

Services own authentication business logic; repositories own
persistence; the API layer owns schemas, cookies, and transport. Routes
never run ad hoc auth queries. Transaction ownership is explicit; a
success audit event is emitted only after a successful commit; a failed
commit never leaves a false success audit. Sensitive exceptions are
translated into controlled errors.

## Audit events

Audited: login success, login failure, logout, session revoked, password
changed, password reset requested, password reset completed, invitation
accepted, selected clinic changed, rate-limited/suspicious attempts.
Never included: passwords, raw session tokens, raw reset/invitation
tokens, cookie values, authorization headers, SQL/database internals.
Success events are transaction-safe.

## Frontend

Pages: `/login`, `/forgot-password`, `/reset-password`, and an invitation
acceptance page if the backend flow is implemented. Login form, logout,
current-user/session loading, unauthenticated redirect, clinic selection
for multiple memberships, current clinic/role display, session-expired
handling, a CSRF-aware fetch helper, change-password form, a neutral
forgot-password success state, reset-password form, invitation
acceptance UX. No password, session token, reset token, or invitation
token is ever stored in `localStorage`. A token present in the URL is
stripped from browser history/state after use where practical. The dev
identity selector is not shown in production.

## Error behavior

Controlled, non-leaking outward errors: invalid login → generic 401;
inactive account → the identical generic login response; password reset
request → identical neutral response; invalid/expired/consumed token →
generic invalid-token response; missing/expired/revoked session →
controlled unauthenticated response plus cookie clearing;
missing/invalid CSRF → controlled 403; rate limit → controlled 429;
invalid clinic selection → a non-enumerating response consistent with
existing tenant policy. No internal exception detail is ever returned.

## Migration

A new MED-004 Alembic migration (never editing the released MED-002/
MED-003 migrations). Normalized-identity uniqueness, session-token-hash
uniqueness, one-time-token-hash uniqueness, explicit foreign keys, useful
expiry/lookup indexes, stable explicit constraint names, lowercase
persisted enum values, safe deletion/revocation behavior. Migration
validation: upgrade from current `master` head to MED-004 head, downgrade
of the MED-004 revision, re-upgrade to head, verification of
tables/indexes/constraints — all against the disposable test database,
confirming the normal development database is never touched.

## Tests

Comprehensive unit and integration tests, including (at minimum): email
normalization; password policy; Argon2 hashing/verification/rehash
detection; session token generation/hashing; reset/invitation token
generation/hashing; session expiry; CSRF; rate limiting; cookie settings;
production rejection of dev identity; error normalization; the full login
→ session → me → logout lifecycle; revoked/expired session rejection;
clinic list/selection tenant-scoping; role reload from the database on
every request; CSRF-missing/invalid/valid mutation behavior; rate-limit
429 without account-existence leakage; change-password requiring the
current password and revoking other sessions; neutral password-reset
request; single-use/expiring reset tokens; reset completion revoking
sessions; invitation tokens being purpose-bound and unable to alter
tenant/role; dev headers failing outside development and never
overriding a production session; transaction-safe audit timing.

Frontend validation relies on the existing tooling (build, lint,
typecheck) plus a manual smoke check — no new test framework is
introduced solely for this task, matching the precedent set in MED-003.

## Security checklist

No plaintext passwords; no persisted raw session/reset/invitation
tokens; no session token in `localStorage`; `HttpOnly` cookie; `Secure`
cookie outside development; CSRF enforced for cookie-authenticated
mutations; session fixation prevented; logout revokes the session;
password change/reset revokes sessions; account enumeration prevented;
login throttling works; inactive users cannot authenticate; inactive
memberships cannot authorize; role changes apply immediately; dev
identity does not work in production; no secrets in logs/audit; no
patient-like test data; `.env` and secrets are never committed.

## Documentation

Update only the relevant parts of `README.md`, `ARCHITECTURE.md`,
`SECURITY.md`, `.env.example`, and this task file — covering session
architecture, cookie policy, CSRF strategy, password policy, session
expiry, development-identity behavior, local authentication testing,
password-reset/invitation limitations, rate limiting, and out-of-scope
items. Never claim production email delivery, MFA, SSO, or production
deployment exist.

## Quality gates and Codex review

Run `.ai-workflow/scripts/run-tests.ps1` and require every configured
check to pass. Run migration validation against the disposable test
database. Run `.ai-workflow/scripts/run-review.ps1` and stop for human
approval — do not automatically repair Codex findings.

## Completion criteria

MED-004 is complete only when: login/logout/session identity work;
password storage is secure; raw session tokens are never persisted;
server-side revocation works; CSRF protection works; brute-force
throttling works; clinic membership resolves server-side; dev identity is
isolated; the password-reset foundation is secure; the invitation-
acceptance foundation is secure; the frontend authentication flow works;
migration validation passes; all quality gates pass; Codex returns
`APPROVED`; no medium/high findings remain; generated reports are not
committed; nothing is pushed, merged, or deployed.
