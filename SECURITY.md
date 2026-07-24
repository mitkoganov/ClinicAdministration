# Security Policy

This document describes the security posture of the **foundation** stage of
this repository. It will be revisited before any real health-data processing
begins.

## Data handling

* No real patient data may be entered into this repository, its databases, its
  seed data, its tests, or any local environment at any stage of foundation
  development.
* All local development data must be synthetic.
* `.env` is never committed. `.env.example` documents required variable names
  only, with placeholder (non-functional) values.

## Secrets

* Secrets are supplied exclusively through environment variables, loaded via
  Pydantic Settings in the backend.
* No secret is ever logged, printed, or included in error responses.
* No secret is read or displayed by AI coding agents operating in this
  repository (see `AGENTS.md`).

## Access control

* Authorization decisions are made **server-side**, never trusted from client
  input.
* Tenant isolation is enforced in the data access layer for every tenant-scoped
  query — a missing or forged tenant identifier must fail closed.
* Principle of least privilege applies to database roles, service accounts,
  and any future API keys.
* **[implemented — foundation]** The tenant/membership domain, request-level
  tenant context, and the demonstration tenant-scoped repository/service
  pattern described in `ARCHITECTURE.md` → "Multi-tenancy".
* **[implemented — MED-004]** Production login/session authentication —
  see `ARCHITECTURE.md` → "Authentication and user identity" and the
  "Authentication threat model" section below. MFA and SSO are still
  **[planned]**.
* **[implemented — MED-005]** Appointments and calendar role matrix
  (`CALENDAR_READ_ROLES`/`CALENDAR_WRITE_ROLES`/`CALENDAR_CONFIG_ROLES`/
  `CALENDAR_OVERRIDE_ROLES`/`CALENDAR_CONTACT_VISIBLE_ROLES`) — see
  `ARCHITECTURE.md` → "Appointments and calendar" and the "Appointments
  and calendar threat model" section below.

## Tenant-boundary threat model

* **Threat: cross-tenant data access (IDOR).** A caller with a valid,
  active membership in Tenant A supplies or guesses an identifier belonging
  to a resource in Tenant B. **Mitigation:** every tenant-owned repository
  method issues a single query with `tenant_id` in the `WHERE` clause (see
  `app.repositories.tenant_scoped_record`) — there is no method that looks
  a row up by `id` alone, so a foreign-tenant row and a nonexistent row are
  structurally indistinguishable to the caller, not just filtered
  after the fact.
* **Threat: tenant/resource enumeration via response differences.** An
  attacker probes whether a tenant or resource exists by comparing error
  codes, error messages, or timing between "doesn't exist" and "exists but
  belongs to someone else." **Mitigation:** every such failure returns the
  identical `404 {"detail": "Not found"}`-shaped response (`NotFoundError`,
  never `403`) — see `app.core.tenant_context.get_tenant_context` and
  `app.services.tenant_scoped_record_service`. Do not add a route or check
  that returns a different status or message for "foreign tenant" versus
  "missing" without deliberately re-reviewing this threat model.
* **Threat: client-supplied tenant/ownership override.** A caller sends a
  `tenant_id` in a request header, route, or JSON body hoping it will be
  trusted. **Mitigation:** the tenant identifier from a header is only ever
  a *claim*, re-validated against the database on every request
  (`resolve_tenant_context`); request body schemas (e.g.
  `TenantScopedRecordCreate`) have no `tenant_id` field at all, so there is
  nothing for a client to override — ownership is always derived
  server-side from the validated context.
* **Threat: insufficient-role privilege escalation.** A caller with a
  read-only or lesser role attempts a mutation. **Mitigation:** the role
  matrix (`app.core.authorization`) is enforced **inside the service
  layer**, not only at the API dependency layer — this is deliberate: a
  service must stay safe to call from any future caller (e.g. a background
  job invoked outside the FastAPI dependency graph), and every rejected
  mutation attempt (cross-tenant or insufficient-role) is captured as a
  rejected audit event.
* **Threat: a clinic left with no active owner.** A demote/deactivate/
  remove request targets a clinic's last active `OWNER` membership,
  including via two concurrent requests that each individually see "not
  the last owner" a moment before both commit. **Mitigation:**
  `StaffService` row-locks (`SELECT ... FOR UPDATE`) every active owner
  membership in the tenant before deciding (`MembershipRepository.
  lock_active_owner_ids`), and rejects with `409 Conflict` if the target
  is the only one — the second of two concurrent requests blocks on the
  lock and re-evaluates against the first request's already-committed
  result, not a stale read.
* **Threat: self-elevation and manager over-reach.** A caller mutates their
  own membership to a higher-privilege role, or a `MANAGER` grants/mutates
  an `OWNER` membership (their own or someone else's) beyond what the role
  matrix in task.md permits. **Mitigation:** `StaffService` independently
  re-derives both checks from the target's *current* role read fresh from
  the database — never from the request payload — before applying any
  change; see `app.services.staff_service._validate_update`.
* **Threat: development identity provider reaching a real environment.**
  `DEVELOPMENT_IDENTITY_ENABLED=true` in a production-like configuration
  would let any caller assert an arbitrary user/tenant via headers.
  **Mitigation:** `Settings` refuses to start at all if this flag is `true`
  while `ENVIRONMENT != "development"` — a fail-closed startup check, not
  a runtime one, and not something request-time logic can silently bypass.

## Authentication threat model

* **Threat: session token theft via database compromise or log leak.**
  **Mitigation:** only a SHA-256 hash of the session token
  (`app.core.session_tokens.hash_token`) is ever persisted; the raw token
  exists only in the `HttpOnly`/`Secure`/`SameSite=Lax` cookie and is never
  logged, printed, or included in an audit event.
* **Threat: JWT-style session forgery or client-side tampering.** Sessions
  are deliberately **opaque server-side tokens, not JWTs** — there is no
  client-decodable claim to forge; every session is looked up against
  `AuthSession` on every request and rejected if revoked or expired
  (idle or absolute).
* **Threat: CSRF on a state-changing request.** **Mitigation:**
  `app.core.csrf.require_csrf` is a session-tied double-submit check —
  the client-supplied `X-CSRF-Token` header must hash-match
  `AuthSession.csrf_token_hash` for *that specific session*
  (`hmac.compare_digest`), not merely match a cookie value, so a token
  belonging to a different valid session cannot be substituted in. Relying
  on `SameSite` alone was explicitly rejected for this reason (see
  `ARCHITECTURE.md` → "CSRF protection").
* **Threat: account-existence enumeration via login timing or response
  differences.** **Mitigation:** `AuthService.login` always calls
  `verify_password` against either the real stored hash or a precomputed
  dummy Argon2id hash, keeping timing statistically indistinguishable
  between "wrong password" and "no such account"; `password-reset/request`
  always returns the same success response regardless of whether the
  email exists.
* **Threat: credential stuffing / brute-force login.** **Mitigation:**
  `app.core.rate_limit.RateLimiter` throttles by (normalized email,
  client IP) via Redis, bounded window not permanent lockout. **Documented
  tradeoff:** it fails open on a Redis outage — availability is
  prioritized over strict throttling for this foundation stage; this must
  be revisited before handling real patient data at scale.
* **Threat: stolen/leaked password reset or invitation link reuse.**
  **Mitigation:** `OneTimeToken` rows are purpose-bound
  (`password_reset`/`invitation`), single-use (`used_at` set atomically on
  consumption), time-limited
  (`password_reset_token_lifetime_minutes`/`invitation_token_lifetime_hours`),
  and only their hash is stored — the raw token is never persisted,
  logged, or echoed back in any API response.
* **Threat: an older/leaked password-reset link surviving a completed
  reset.** A user requests more than one reset link (e.g. forgets they
  already asked, or a leaked older link is later attempted). **Mitigation:**
  `PasswordResetService` keeps at most one outstanding reset token per
  account at any time — requesting a new one revokes every older
  outstanding token first (`OneTimeTokenRepository.
  revoke_all_password_reset_for_user`), and completing a reset revokes
  every *other* outstanding token for that account in the same
  transaction as the password update and token consumption. A stale or
  leaked reset link can never be used to take the account over again
  after a reset has already completed.
* **Threat: password-change or logout leaving other stolen sessions
  active.** **Mitigation:** `AuthService.change_password` revokes every
  *other* session for that user in the same transaction as the password
  update (`SessionService.revoke_all_for_user`).
* **Threat: dev-identity header used to bypass or shadow a real session.**
  **Mitigation:** `get_tenant_context` always resolves a real session
  first; a dev header is only ever consulted when **no session cookie was
  sent at all** — a cookie that was sent but is invalid, expired, revoked,
  or belongs to a now-inactive account is never treated as absent, and
  never falls back to dev headers either (see `ARCHITECTURE.md` →
  "Dev-identity isolation"). Covered by dedicated integration tests
  asserting a dev header is ignored whenever a valid session cookie is
  present, and separately that an *invalid* session cookie still 401s
  with cookie clearing even when valid dev headers are also sent.
* **Threat: insecure session cookie reaching a production-like
  environment.** **Mitigation:** `Settings._validate_cookie_security`
  refuses to start if `session_cookie_secure=False` outside
  `environment=development` — the same fail-closed-at-startup pattern as
  the existing dev-identity validator, not a runtime-only check.

## Development identity restrictions

* The `X-Dev-User-Id` / `X-Tenant-Id` header-based identity provider
  (`app.core.identity`) exists only to unblock tenant-context development
  and testing before real authentication exists. It is disabled by default.
* It must never be enabled in any environment other than
  `ENVIRONMENT=development` — enforced at application startup, not left to
  operator discipline.
* Values it extracts are never trusted as authorization by themselves; they
  are always re-validated against the database before any access is
  granted.
* It is covered by tests, including the case where it is disabled and
  headers are supplied anyway (must still reject).
* The frontend entry point for configuring a development identity,
  `/dev/identity`, returns a real, server-enforced HTTP 404 in any
  non-development build (a Next.js Server Component calling `notFound()`
  — see `ARCHITECTURE.md` → "Dev-identity isolation") — not merely a
  page that renders a "not found" message at status 200. Neither
  `localStorage` nor a query parameter can make it reachable in a
  production build; the gate runs entirely server-side before any
  selector UI would be sent to the browser.

## Fail-closed background operations

* The background-job tenant context (`app.core.background_context`) fails
  closed: constructing one from an incomplete or malformed payload (missing
  `tenant_id` or `actor_user_id`, invalid identifiers) raises rather than
  returning a partially-valid context.
* A worker receiving a `BackgroundTenantContext` must re-resolve and
  revalidate tenant and membership state via `app.services.tenant_service`
  before performing any tenant-owned action — the context alone is never
  sufficient authorization, exactly like the request-level `TenantContext`.
* No job queue exists yet; this defines the contract a future one must
  honor.

## Mandatory cross-tenant regression testing

* Every tenant-isolation-relevant test fixture set uses **at least two
  tenants** with **overlapping role coverage**, per
  `backend/tests/factories.py` (`Tenancy`): Tenant A and Tenant B, a user
  with active membership in both, an inactive tenant, an inactive
  membership, and one `TenantScopedRecord` per tenant.
* `backend/tests/integration/test_tenant_scoped_records_api.py` asserts
  that a cross-tenant 404 and a missing-resource 404 return byte-identical
  response bodies (`test_error_responses_do_not_distinguish_missing_from_foreign_tenant`)
  — a regression here would silently reopen the enumeration threat above.
* Any new tenant-owned resource or route must add the equivalent
  isolation, role-matrix, and audit-event tests before merging — do not
  rely on manual testing for this class of bug.

## Appointments and calendar threat model (MED-005)

* **Threat: double-booking via a race between two concurrent requests.**
  Mitigated in two layers: a service-layer availability pre-check (racy by
  itself, since it reads without locking), backed by an authoritative
  PostgreSQL exclusion constraint (`ex_appointments_provider_overlap`/
  `ex_appointments_room_overlap`, `EXCLUDE USING gist` over `tstzrange`)
  that is the actual guarantee — verified with two genuinely independent
  database connections/threads in
  `backend/tests/integration/test_appointment_concurrency.py`, not a
  simulated sequential call. See `ARCHITECTURE.md` → "Appointments and
  calendar" → "Double-booking prevention: two layers, not one".
* **Threat: lost-update on appointment mutation (two staff members editing
  the same appointment concurrently).** Mitigated by optimistic locking
  (`Appointment.version`, enforced by a single atomic `UPDATE ... WHERE
  ... AND version = :expected_version`) — a stale write is rejected with
  `409 stale_version`, never silently overwritten.
* **Threat: patient contact data exposure to a role that shouldn't see
  it.** `AUDITOR` may read the calendar (appointment existence, timing,
  status, `patient_display_name`) but the patient contact snapshot
  (phone/email specifically) is redacted at serialization
  (`app.api.appointments._serialize` returns `AppointmentSummaryRead`,
  which omits `patient_phone`/`patient_email` entirely rather than a
  `null`-filled `AppointmentRead`) for any role outside
  `CALENDAR_CONTACT_VISIBLE_ROLES`. `CONTENT_EDITOR` has no calendar
  access at all. `_serialize` is the SINGLE place every appointment
  response (read, list, create, metadata update, reschedule, every
  lifecycle action) goes through — no route may call
  `AppointmentRead.model_validate(...)` directly, which would bypass
  this policy for whichever endpoint did it (fixed in a second MED-005
  repair round: an earlier version had exactly two such bypasses —
  (1) `_serialize` itself granted full contact visibility whenever the
  caller happened to be the appointment's own provider, regardless of
  role, so an `AUDITOR` acting as their own appointment's provider saw
  full phone/email; (2) the redacted schema additionally omitted
  `patient_display_name`/`notes` entirely, which over-redacted — the
  task requires only the contact snapshot to be hidden, not the
  appointment's own identifiability in a calendar view. Both are
  regression-tested in `backend/tests/integration/test_appointments_api.py`
  — e.g. `test_auditor_provider_still_does_not_see_own_appointment_contact_info`
  and `test_auditor_sees_redacted_summary_not_contact_info`).
* **Threat: an appointment's own provider editing its patient contact
  snapshot/notes without a write role.** `AppointmentService.update_metadata`
  has **no** self-scoped bypass — unlike `complete`/`no_show`, being the
  appointment's provider does not itself grant metadata-edit rights; every
  caller needs `CALENDAR_WRITE_ROLES` (fixed in the MED-005 repair round —
  an earlier version incorrectly extended the complete/no-show bypass to
  metadata edits too, which would have let e.g. an `AUDITOR` acting as
  their own appointment's provider rewrite the patient's phone/email).
  Regression-tested in
  `backend/tests/integration/test_appointments_api.py` (self-provider
  without a write role gets `403`).
* **Threat: over-restrictive availability lookup blocking legitimate
  self-service.** `GET /api/v1/availability` allows any active member to
  query availability for themselves as the provider
  (`require_calendar_read_or_self`), regardless of role — task.md's
  authorization matrix requires this self-scope carve-out; an earlier
  version incorrectly rejected every caller outside `CALENDAR_READ_ROLES`
  even for their own schedule (fixed in the MED-005 repair round). The
  self-scope comparison is always against the server-resolved
  `TenantContext.user_id`, never a client-supplied claim — a caller cannot
  spoof another provider's identity via the `provider_id` query parameter
  to gain that provider's availability view.
* **Threat: metadata PATCH unable to clear previously-set PII, or
  silently smuggling a room change past validation.** `AppointmentMetadataUpdate`
  distinguishes "field omitted" from "field explicitly `null`" via
  Pydantic's `model_fields_set` (not a plain `if value is not None`
  filter, which cannot tell the two apart) — a caller can clear
  `patient_phone`/`patient_email`/`notes`/`room_id` by sending an
  explicit `null`, while an omitted field is left untouched (fixed in a
  second MED-005 repair round: an earlier version filtered out every
  `None` value unconditionally, so a previously-set phone/email/notes
  could never be removed). `room_id` changes go through the same
  tenant/active-room/availability validation as `POST`/`reschedule`
  (never a bare column write) — an inactive room is `409
  room_unavailable`, a cross-tenant room is `404`, and a genuinely
  occupied room/time is `409 appointment_conflict`/`outside_schedule`
  via the same `AvailabilityService`/DB-exclusion-constraint pair
  documented above, not a separate, weaker check.
  `patient_display_name` cannot be cleared (rejected at the schema
  layer with `422`) since it is not nullable on the `Appointment` model.
* **Threat: booking outside a provider's authorized availability going
  unnoticed.** The `override_availability` escape hatch is restricted to
  `CALENDAR_OVERRIDE_ROLES` (owner/manager) only, requires a non-empty
  `override_reason`, and always emits a distinct `appointment.override_used`
  audit event in addition to the normal `appointment.created`/
  `appointment.rescheduled` event — an override is never silent.
* **Threat: unbounded query ranges used for denial-of-service or excessive
  data exposure.** `GET /api/v1/availability` and `GET
  /api/v1/calendar-blocks` both reject a `date_from`/`date_to` range wider
  than 31 days (`409 Conflict`) — there is no unbounded "give me everything"
  query path.
* **Threat: booking a stale/inactive resource (deactivated room, service
  type, or staff membership).** Every create/reschedule call re-validates
  the provider's membership, the service type, and the room (if any) are
  `active` at the time of the call — a resource deactivated after an
  appointment was created is not retroactively affected, but no NEW
  booking or reschedule can be made against an inactive one.
* **Tenant isolation** follows the same pattern as every other MED-003+
  module: every repository method scopes by `tenant_id` in one query (see
  "Tenant-boundary threat model" above) — cross-tenant appointment/room/
  schedule/block access returns the same 404 as a nonexistent id.

## Logging

* Logging is structured (not free-text patient data).
* Logs must never contain patient-identifying information, credentials, or
  full request/response bodies once real data exists.
* Redaction is applied at the logging boundary, not left to callers to
  remember.

## Audit

* Once the `audit` module (a persistent, immutable store) exists, every
  state-changing business action must produce an audit record capturing
  actor, tenant, action, and timestamp.
* **[implemented — foundation, interim]** `app.core.audit.emit_audit_event`
  is a structured-logging adapter behind a stable call site — see
  "Audit-event flow" in `ARCHITECTURE.md`. It already captures event type,
  tenant id, actor user id, target resource type/id, outcome, timestamp,
  and correlation id for every tenant-scoped mutation (success and
  rejection), but is not yet a durable/immutable store.

## AI-specific restrictions

* LLM components are restricted to language understanding and response
  composition only — see "AI boundary" in `ARCHITECTURE.md`.
* An LLM must never be permitted to make a diagnostic or clinical decision.
* An LLM must never directly execute a database write or scheduling action;
  all actions go through deterministic, independently-authorized services.

## Dependency maintenance

* Dependencies are pinned via lock files (`requirements`/`uv.lock` or
  equivalent for backend, `package-lock.json` for frontend).
* Vulnerability warnings from `pip`, `npm audit`, or Dependabot-equivalent
  tooling are not suppressed; they are triaged.

## GDPR-oriented design

* Data minimization: only fields required for a specific, scoped feature are
  modeled — no speculative "just in case" personal fields.
* Right to erasure and data portability are architectural concerns for the
  `tenants`/`practitioners`/`conversations` modules once implemented, not an
  afterthought.

## Backups & incident response (planned)

* Production backup strategy, retention, and restore testing must be defined
  before any production deployment — out of scope for the foundation stage.
* An incident response runbook must exist before real patient data is
  processed.

## Authentication known limitations

* No email delivery exists for password-reset or invitation links — the
  backend issues and validates tokens, but nothing sends the email. Do not
  claim email delivery, MFA, or SSO exist anywhere in operator-facing
  material until they are actually implemented.
* No production deployment, secret-rotation, or monitoring work has been
  done for the auth subsystem specifically; this remains foundation-stage
  code pending a real threat-model review before real patient data.

## Threat modeling

* A formal threat model must be produced and reviewed before this platform
  processes any real health data. The foundation stage intentionally excludes
  authentication and data processing to keep the attack surface minimal until
  that review happens.

## Vulnerability reporting

* During local foundation development, report issues by opening a task in
  `tasks/` describing the issue, impact, and suggested remediation. A formal
  external reporting channel will be defined before any deployment.
