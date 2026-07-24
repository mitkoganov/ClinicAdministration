# Architecture

## Current stage

This repository currently contains a **foundation only**: a modular monolith
skeleton with no business logic, no authentication, and no patient data
handling. Everything below is marked either **[implemented]** or **[planned]**.

## Style: modular monolith first

The platform starts as a single deployable backend service organized into
internal modules with clear boundaries, and a single frontend application.
Splitting into separate services is deferred until a module's independent
scaling or ownership needs justify the operational cost. Module boundaries are
designed so that extraction later does not require a rewrite.

## Multi-tenancy

**[implemented — foundation]** Tenancy model: a clinic (or clinic group) is a
tenant. All tenant-scoped tables carry a `tenant_id`. Tenant isolation is
enforced **server-side** in the data access layer — never trusted from client
input, never inferred solely from a URL parameter. This foundation stage
implements the tenant/membership domain, request-level context resolution,
repository scoping, authorization primitives, background-job context, and a
minimal audit abstraction — it does **not** implement any business module
(clinics, practitioners, appointments, ...); those are separate future work.

### Tenant domain

* `Tenant` (`backend/app/models/tenant.py`): `id` (UUID), `name`, `slug`
  (unique, normalized via `app.services.tenant_service.normalize_slug` —
  lowercased, non-alphanumeric runs collapsed to a single hyphen), `status`
  (`active`/`inactive`), timestamps (`DateTime(timezone=True)`).
* `TenantMembership` (`backend/app/models/membership.py`): connects a
  `user_id` (a bare UUID column with **no** foreign key — kept
  unconstrained deliberately rather than adding a cross-migration FK to
  this already-released migration once `UserAccount` was added in MED-004;
  see "Authentication and user identity" below) to a `tenant_id`, with a `role`
  (`owner`/`manager`/`operator`/`content_editor`/`auditor`) and a `status`
  (`active`/`inactive`). Unique on `(tenant_id, user_id)`.
* Status and role enums use `sqlalchemy.Enum(..., native_enum=False)` — a
  `VARCHAR` column with a `CHECK` constraint, not a Postgres-native enum
  type — so future value changes are a plain migration, not `ALTER TYPE`.

### Tenant context resolution

`app.core.tenant_context.get_tenant_context` is the single reusable FastAPI
dependency every tenant-scoped route depends on. It never trusts a
client-supplied tenant identifier by itself:

1. A development-only identity provider (`app.core.identity`, see
   "Development identity" below) extracts a caller-supplied `user_id` and
   `tenant_id` from the `X-Dev-User-Id` / `X-Tenant-Id` request headers.
2. `app.services.tenant_service.resolve_tenant_context` re-validates,
   against the database, that the tenant exists and is `active`, and that
   the user has an `active` membership in it.
3. Any failure — unknown tenant, inactive tenant, missing membership,
   inactive membership — raises the same `NotFoundError` (HTTP 404). These
   failure modes are deliberately indistinguishable to the caller (see
   "Secure cross-tenant behavior" in `SECURITY.md`).
4. The resulting `TenantContext` (user id, tenant id, tenant name,
   membership id, role, membership status) is a plain per-request object
   threaded through `Depends()` — there is no global mutable tenant
   variable anywhere in the codebase.

### Membership validation

Tenant and membership active-state checks live in exactly one place —
`app.services.tenant_service` (`resolve_tenant`, `resolve_membership`,
`resolve_tenant_context`) — reused by both the request-level context
dependency and, in the future, background jobs, so the validation rules
cannot drift between the two call sites.

### Repository scoping

Tenant-owned repositories (see `app.repositories.tenant_scoped_record` for
the demonstration pattern) take `tenant_id` as an explicit argument on every
method and issue a **single** query with `tenant_id` in the `WHERE` clause.
There is deliberately no method that looks a row up by `id` alone: a
foreign-tenant row and a missing row must produce the identical "no row"
result from one query, never a lookup followed by a tenant comparison —
the latter would briefly determine foreign-tenant existence even if never
returned to the caller. No tenant-owned repository exposes an unscoped
`list_all`.

### Authorization flow

`app.core.authorization` defines the role matrix (`READ_ROLES` = every
role; `WRITE_ROLES` = owner/manager/operator/content_editor;
`DELETE_ROLES` = owner/manager) and two ways to apply it:

* `require_roles(...)` — a FastAPI dependency factory used at the API layer
  as an early-rejection convenience.
* `require_role(context, allowed)` — the same check, called directly inside
  each service method.

**The service layer is the authoritative authorization boundary.** Mutating
routes (`create`/`update`/`delete` on `/api/v1/tenant-resources`) intentionally
depend only on `get_tenant_context`, not on `require_roles`, and let the
service perform (and audit) the role check — this both keeps the service
independently safe to call from any future caller (e.g. a background job)
and ensures insufficient-role rejections are audited (see "Audit-event
flow"). Read routes still use `require_roles(*READ_ROLES)` as a harmless
early-rejection convenience, since read rejections are not required to be
audited.

### Development identity

`app.core.identity.get_raw_identity` is an explicitly non-production
identity provider: it reads `X-Dev-User-Id` and `X-Tenant-Id` headers, and
is disabled by default (`DEVELOPMENT_IDENTITY_ENABLED=false`). `Settings`
refuses to start at all if `DEVELOPMENT_IDENTITY_ENABLED=true` while
`ENVIRONMENT` is not `development` — a fail-closed startup check, not just a
runtime one. The values it extracts are never trusted as authorization by
themselves; `get_tenant_context` always re-validates them against the
database. This provider is meant to be replaced wholesale by the future
authentication module without changing any tenant-scoped route.

### Background-job tenant context

`app.core.background_context.BackgroundTenantContext` is a minimal,
serializable dataclass (`tenant_id`, `actor_user_id`, optional
`correlation_id`) for a future job queue — no queue exists yet. It carries
only identifiers, never a database or membership object.
`from_dict`/`to_dict` round-trip it for transport, and `from_dict` fails
closed (raises `AppError`) if `tenant_id` or `actor_user_id` is missing or
malformed. **Workers must re-resolve and revalidate tenant/membership state
via `app.services.tenant_service` before performing any tenant-owned
action** — this context is not authorization by itself, exactly like the
request-level `TenantContext`.

### Audit-event flow

`app.core.audit.emit_audit_event` is the single, stable audit call site.
No persistent audit store exists yet; the interim adapter is a structured
logging sink (`logger.info("audit_event", extra={...})` on the `"audit"`
logger) — replace the sink inside `emit_audit_event` with a durable audit
repository later without changing any caller. `TenantScopedRecordService`
emits an event for every successful create/update/delete, every rejected
cross-tenant attempt, and every rejected insufficient-role attempt. Audit
events never contain secrets, request bodies, or healthcare data.

### Secure cross-tenant behavior

Cross-tenant access always returns **404**, never 403 — an
authenticated-but-wrong-tenant caller must not be able to distinguish "this
resource doesn't exist" from "this resource belongs to someone else." This
is implemented once, at the repository query level (see "Repository
scoping"), not re-implemented per route.

### Demonstration entity

`TenantScopedRecord` (`backend/app/models/tenant_scoped_record.py`) exists
solely to exercise and test the pattern above end to end. It is explicitly
**not** a business-domain entity and carries no clinic/practitioner/patient
data — future business modules get their own models that follow this same
pattern, not this one.

## Clinic and staff administration

**[implemented — MED-003]** The first business-facing vertical slice built
on the multi-tenant foundation above. "Clinic" is user-facing terminology
for the existing `Tenant` model — a clinic maps one-to-one to a tenant;
no separate `clinics` table exists, and the tenant/membership migration
history is unchanged.

* `GET /api/v1/clinic` / `PATCH /api/v1/clinic`
  (`backend/app/api/clinic.py`, `app/services/clinic_service.py`) — view/edit
  the caller's own clinic. Only the display name is editable; `PATCH`
  schemas (`app/schemas/clinic.py`) use `extra="forbid"` so status, slug, id,
  and audit metadata can never be set via this endpoint. Only `OWNER` may
  edit; every other active role may view (see "Authorization flow").
* `GET/POST /api/v1/clinic/staff`, `PATCH/DELETE
  /api/v1/clinic/staff/{membership_id}` (`backend/app/api/staff.py`,
  `app/services/staff_service.py`) — administer `TenantMembership` rows for
  the caller's own clinic. Listing supports role/status filters and
  pagination, always additionally scoped by `tenant_id` (see "Repository
  scoping"). Every membership lookup by id is tenant-scoped in one query,
  so a cross-tenant or nonexistent `membership_id` returns the identical
  404 (see "Secure cross-tenant behavior").
* **Role matrix**: `OWNER` may invite/change/deactivate/remove any role.
  `MANAGER` may invite only `OPERATOR`/`AUDITOR`, and can never create,
  promote to, or mutate an `OWNER` membership. `OPERATOR`/`CONTENT_EDITOR`/
  `AUDITOR` can never mutate staff; `AUDITOR` (plus `OWNER`/`MANAGER`) can
  read the staff roster, `OPERATOR`/`CONTENT_EDITOR` cannot — that "minimum
  staff information" view for operators described in task.md is not yet
  implemented (see "Known limitations" below).
* **Self-elevation**: no one can use this endpoint to increase their own
  role's privilege tier (`app.services.staff_service._ROLE_RANK`) — a
  deliberately coarse, three-tier ranking used only for this one check.
  Voluntary self-*demotion* (e.g. an owner stepping down to manager) is
  allowed, subject to the final-owner invariant below.
* **Final-owner invariant**: a clinic can never end up with zero active
  owners. Before demoting/deactivating/removing an `OWNER` membership, the
  service row-locks (`SELECT ... FOR UPDATE`) every currently-active owner
  membership in that tenant (`MembershipRepository.lock_active_owner_ids`)
  and rejects with `409 Conflict` if the target is the only one — this
  closes the race where two concurrent requests each see "not the last
  owner" a moment before both commit.
* **Removal is soft deactivation**, not a physical `DELETE FROM`: `status`
  is already the documented membership lifecycle (see "Membership
  validation"), a deactivated membership administers nothing (it fails
  `resolve_membership`'s active-status check immediately), and no other
  record holds a foreign key to a membership row that a hard delete could
  break. `DELETE /api/v1/clinic/staff/{id}` and `PATCH ... {"status":
  "inactive"}` have the same effect on the row; `DELETE` additionally emits
  a `membership.removed` audit event distinct from `membership.deactivated`.
* **Audit events**: `clinic.update`, `membership.create`,
  `membership.role_changed`, `membership.activated`,
  `membership.deactivated`, `membership.removed` — each following the same
  commit-then-audit pattern as the MED-002 demonstration service (see
  "Audit-event flow"), plus a `rejected` event for every insufficient-role,
  duplicate-membership, and final-owner rejection.
* `frontend/app/settings/clinic` and `frontend/app/settings/staff` — the
  first real admin UI pages (see "Implemented components" below).

### Known limitations

* No email-invitation delivery exists. "Adding staff" provisions a
  membership for a `user_id` the caller already knows (a development/test
  identity, in this foundation stage) — it is not an invitation flow, and
  nothing in this slice claims otherwise.
* `OPERATOR`'s staff visibility is task.md's "minimum staff information
  explicitly required by the application" — no current feature requires
  operators to see any staff data, so this slice gives them none (403 on
  `GET /api/v1/clinic/staff`) rather than inventing an unused partial view.

## Authentication and user identity

**[implemented — MED-004]** Production login/session foundation, layered on
top of the tenant/membership domain above and independent of the
development-only identity provider. This slice adds real user accounts and
server-side sessions; it does **not** add email delivery, MFA, SSO, or a
production deployment/observability story — see "Known limitations" below.

### Account and session model

* `UserAccount` (`backend/app/models/user_account.py`): `id` (UUID),
  `normalized_email` (unique, lowercased/trimmed), `password_hash`
  (Argon2id, see "Password hashing"), `display_name`, `status`
  (`active`/`inactive`), timestamps. A `UserAccount` has no required
  relationship to any `TenantMembership` — an account can exist, and log
  in, with zero clinic memberships (see task.md: "login може да успее без
  membership"). Membership rows continue to reference `user_id` as a bare
  UUID, now meaningfully resolvable to a real account instead of a
  placeholder header.
* `AuthSession` (`backend/app/models/auth_session.py`): server-side opaque
  session record — `id`, `user_id`, `token_hash` (SHA-256 of the raw
  session token, never the raw token itself), `csrf_token_hash` (see
  "CSRF protection"), `selected_tenant_id` (nullable — the caller's chosen
  clinic, stored server-side, never client-asserted per request),
  `created_at`, `idle_expires_at`, `absolute_expires_at`, `revoked_at`.
  **The session never caches a role** — role is re-resolved from
  `TenantMembership` on every request that needs it (see `GET /auth/me`),
  so a role change or removal takes effect immediately, not at next login.
* `OneTimeToken` (`backend/app/models/one_time_token.py`): purpose-bound
  (`password_reset` / `invitation`) single-use token — `id`, `user_id`,
  `token_hash`, `purpose`, `expires_at`, `used_at`. The raw token is never
  persisted, logged, or returned in any API response body; it exists only
  in the URL a user follows (see "Known limitations" on delivery). At
  most one `password_reset` token is ever outstanding per account:
  `PasswordResetService.request_reset` revokes any older outstanding one
  before issuing a new one, and `confirm_reset` revokes every *other*
  outstanding one (in the same transaction as the password update and
  the submitted token's own consumption) — an older or leaked reset link
  can never survive a completed reset (see SECURITY.md).

### Session lifecycle and cookies

Sessions are **server-side opaque tokens, never JWTs** — no session claim
is ever decoded client-side or trusted from a client-presented payload.
`app.core.session_tokens.generate_token()` produces the raw token
(`secrets.token_urlsafe(32)`); only its SHA-256 hash
(`hash_token()`) is stored, so a leaked database row cannot be replayed as
a session by itself. The raw token is set via `Set-Cookie` with
`HttpOnly`, `SameSite=Lax`, and `Secure` (`session_cookie_secure`,
default `true` — see "Cookie security fail-closed" below).

* **Idle vs. absolute expiry**: `session_idle_lifetime_hours` (default 24)
  expires a session unused for that long; `session_absolute_lifetime_hours`
  (default 7 days) is a hard cap regardless of activity. Both are enforced
  on every request in `app.core.session_dependency`, fail-closed (an
  expired-either-way session is treated as no session at all, not
  silently extended).
* **Login** (`POST /api/v1/auth/login`) issues a new session + CSRF token
  pair. **Logout** (`POST /api/v1/auth/logout`) is idempotent for every
  "no usable session" condition — missing, unknown, expired, revoked, or
  an inactive-account session cookie all clear both cookies and return
  success, never revealing which one applied. It deliberately does not
  use the shared `require_csrf` dependency (that would 401 a stale
  cookie instead of the idempotent success above — see
  `app.core.session_dependency.get_current_session_or_none_if_stale`);
  CSRF is still required, via the same comparison
  (`app.core.csrf.enforce_csrf_for_valid_session`), whenever there IS a
  genuinely valid session — a missing/invalid CSRF token there is a
  controlled 403 that revokes nothing and clears nothing. A valid
  session's own revocation is server-side (`revoked_at`), not just
  client-side cookie clearing.
* **Clinic selection** (`POST /api/v1/auth/select-clinic`) re-validates
  that the caller has an active membership in the requested tenant before
  storing `selected_tenant_id` on the session row — the same
  indistinguishable-404 pattern as the tenant-context resolver above
  applies here (no membership and a nonexistent tenant both reject
  identically).
* **Password change** (`POST /api/v1/auth/change-password`) requires the
  current password and revokes all *other* sessions for that user
  (`SessionService.revoke_all_for_user`) in the same transaction as the
  password update — this method deliberately does not commit itself, so
  the caller controls the transaction boundary and a failed password
  update cannot leave other sessions revoked.

### Password hashing

`app.core.passwords` uses **Argon2id** (`argon2-cffi`), not bcrypt/PBKDF2.
Policy (`validate_password_policy`): minimum 12 characters, maximum 256,
**no forced character-class mixing** (task.md is explicit that composition
rules are not a hardening measure — length is). `needs_rehash()` lets a
future parameter-cost increase re-hash existing accounts transparently on
their next successful login, without a bulk migration.

`verify_password` never raises — a malformed stored hash or unexpected
input returns `False`, not an exception a caller might mishandle into a
silent auth bypass. Login always calls `verify_password` against either
the real stored hash or a precomputed dummy Argon2id hash
(`_DUMMY_PASSWORD_HASH`, computed once at import time) when the account
doesn't exist — keeping response timing statistically indistinguishable
between "wrong password" and "no such account," closing a timing-based
account-enumeration side channel.

### CSRF protection

`app.core.csrf.require_csrf` implements **double-submit tied server-side
to the session**, not a bare cookie-equals-header check and not reliance
on `SameSite` alone (task.md is explicit that `SameSite`-only protection
is insufficient here). `AuthSession.csrf_token_hash` stores the hash of
the CSRF token issued at login; the client must echo the raw token via the
`X-CSRF-Token` header, which the dependency hashes and compares
(`hmac.compare_digest`) against the session's stored hash — a token from a
*different* valid session cannot be substituted in, unlike plain
double-submit. `require_csrf` is a no-op for safe methods and for requests
with no valid session (the route's own auth dependency independently
rejects unauthenticated callers regardless) — applied to
`select-clinic`/`change-password` and to the MED-003 mutating
clinic/staff routes, not to `login`/`password-reset`/`invitation-accept`,
which have no session yet to tie a token to. `logout` enforces the exact
same comparison via `enforce_csrf_for_valid_session` directly rather than
the `require_csrf` dependency, since it must stay idempotent for a stale
session cookie instead of 401ing it (see "Session lifecycle and cookies"
above).

### Login rate limiting

`app.core.rate_limit.RateLimiter` throttles login attempts per
(normalized email, client IP) pair via a Redis-backed counter
(`login_rate_limit_max_attempts`, default 5, per
`login_rate_limit_window_seconds`, default 15 minutes) — a bounded
sliding window, never a permanent lockout. It is defined against a
`RateLimitStore` `Protocol`, not a concrete Redis type, so tests inject a
deterministic in-memory fake instead of requiring Redis. **Fails open on
Redis outage**: `check_and_consume`/`reset` catch any Redis error and
allow the request through rather than locking every caller out because a
cache is unavailable — explicitly documented as a deliberate
availability-over-strictness tradeoff for this foundation stage, not an
oversight.

### Dev-identity isolation

The development-only identity provider (`app.core.identity`, see
"Development identity" above) and real sessions are fully independent
mechanisms that must never conflict. `app.core.tenant_context.
get_tenant_context` resolves a real session first — by calling
`get_current_session_optional` directly inside the function body, not via
a stacked `Depends` (FastAPI dependencies have no native "try A, fallback
B" composition) — and only falls back to `X-Dev-User-Id`/`X-Tenant-Id`
headers when there is **no session cookie at all**. A session cookie that
was actually sent but turned out invalid/expired/revoked (or belongs to a
now-inactive account) never falls back either: `get_current_session_
optional` re-raises `InvalidSessionError` for that case instead of
returning `None` (see `app.core.session_dependency`), so it reaches the
same 401 + cookie-clearing response as any other stale-session request
and dev headers are never even consulted. **A valid session always
wins, and an invalid one is never treated as absent.** This makes every
existing MED-002/MED-003 tenant-scoped route session-aware automatically,
without editing those route files.

The frontend's own development-identity entry point, `/dev/identity`
(`frontend/app/dev/identity/page.tsx`), is a Server Component that calls
Next's `notFound()` outside a development build — an actual HTTP 404 for
every production request, never a client component quietly rendering a
"not found" message at status 200. The selector UI itself lives in a
separate client component (`dev-identity-page-client.tsx`) the server
page never renders in production, so neither a crafted `localStorage`
value nor a query parameter can reach it.

### Cookie security fail-closed

`Settings._validate_cookie_security` (a `model_validator`, same pattern as
the existing dev-identity validator) refuses to start at all if
`session_cookie_secure=False` while `environment != "development"` — a
non-`Secure` session cookie can only ever exist in local plain-HTTP
development, never silently in a production-like configuration.

### API endpoints

All under `/api/v1/auth` (`backend/app/api/auth.py`):

| Method & path | Session required | CSRF | Purpose |
|---|---|---|---|
| `POST /login` | no | no | Issue session + CSRF cookies |
| `POST /logout` | optional | only if session valid | Idempotent session revocation |
| `GET /me` | yes | — | Current user + selected clinic (role re-resolved live) |
| `GET /clinics` | yes | — | All active clinic memberships for the caller |
| `POST /select-clinic` | yes | yes | Set `selected_tenant_id` on the session |
| `POST /change-password` | yes | yes | Verify current password, set new one, revoke other sessions |
| `POST /password-reset/request` | no | no | Issue a `OneTimeToken` (purpose `password_reset`); response never reveals whether the email exists |
| `POST /password-reset/confirm` | no | no | Consume the token, set new password |
| `POST /invitations/accept` | no | no | Consume an invitation `OneTimeToken`, create the account, log in |

### Known limitations

* **No email delivery exists.** Password-reset and invitation tokens are
  generated and stored server-side, but nothing sends the email
  containing the reset/invitation link — this slice stops at "the backend
  can issue and validate the token," matching the same
  provision-not-invite pattern already documented for MED-003 staff
  additions. Do not claim otherwise in operator-facing text.
* **No MFA, no SSO.** Login is single-factor, password-only.
* **No production deployment/observability work.** Session/cookie config
  is fail-closed by default, but rollout, secret rotation, and monitoring
  for the auth subsystem specifically are out of scope for this
  foundation slice.

## Appointments and calendar

**[implemented — MED-005]** The first scheduling/booking vertical slice,
layered on the tenant/membership and clinic/staff foundations above. There
is still no dedicated `Practitioner` model — "provider" is a fact (a
`TenantMembership.user_id` referenced by a schedule/appointment row), not a
role or a separate identity; that distinct practitioner-records module
remains **[planned]**, and this slice does not add billing, patient-record
storage, or any notification delivery (see "Known limitations" below).

### Domain model

* `Tenant.timezone` (`backend/app/models/tenant.py`) — an IANA name
  (`Europe/Sofia` default), validated via `zoneinfo.ZoneInfo` at the model
  layer (`app.core.timezone.validate_timezone_name`). Every wall-clock
  calculation for that tenant (schedules, availability, calendar-day
  boundaries) resolves through this column — there is no per-request or
  per-request-header timezone override.
* `ClinicRoom`, `AppointmentServiceType` (`backend/app/models/clinic_room.py`,
  `appointment_service_type.py`) — tenant-scoped configuration rows with a
  two-value `status` (`active`/`inactive`, never a hard delete) and a unique
  `(tenant_id, code)`. A service type carries its own
  `default_duration_minutes` and `buffer_before_minutes`/
  `buffer_after_minutes`; there is deliberately no price/currency/billing
  field.
* `ProviderSchedule` + `ScheduleBreak` (`backend/app/models/provider_schedule.py`)
  — a recurring weekly rule (`day_of_week` 0=Monday, matching
  `datetime.weekday()`) with an effective date range and an optional room.
  Overlap rejection for two rules of the same provider is **service-layer
  only** (`ScheduleService._validate_no_overlap`), not a DB exclusion
  constraint — combining a day-of-week recurrence with a date range and a
  time range into one PostgreSQL exclusion constraint was judged not worth
  the complexity for a foundation slice; this is a deliberate, documented
  scope reduction, not an oversight. `ScheduleBreak` rows carry no
  `tenant_id` of their own, scoping only through their parent schedule
  (`ondelete=CASCADE`) — an intentional deviation from the "every table has
  its own `tenant_id`" convention, since a break has no independent
  existence outside its schedule.
* `CalendarBlock` (`backend/app/models/calendar_block.py`) — an ad hoc
  provider- and/or room-level block (leave, training, maintenance, ...)
  that is never bookable over, checked by the same availability engine as
  the recurring schedule.
* `Appointment` (`backend/app/models/appointment.py`) — the booking record.
  `status` (`scheduled`/`confirmed`/`cancelled`/`completed`/`no_show`),
  `version` (optimistic-locking counter, starts at 1), and a denormalized
  patient contact snapshot (`patient_display_name`/`patient_phone`/
  `patient_email`) captured at booking time — there is no separate patient
  record/profile in this slice, matching the "no patient data handling yet"
  foundation-stage boundary.

### Double-booking prevention: two layers, not one

* **Service-layer pre-check** (`AvailabilityService.is_interval_free`) —
  computed dynamically from the provider's recurring schedule, breaks,
  active calendar blocks, and existing blocking appointments; never
  materializes a slot as a database row. This is what a sequential
  double-booking attempt hits, surfaced as `409 outside_schedule`.
* **Database exclusion constraint** (the migration's
  `ex_appointments_provider_overlap`/`ex_appointments_room_overlap`, using
  PostgreSQL's `EXCLUDE USING gist` over `tstzrange(starts_at, ends_at,
  '[)')`, requiring the `btree_gist` extension for the `uuid`/`varchar`
  equality terms) is the **authoritative** layer: a genuine race between
  two concurrent transactions that both pass the pre-check is caught here,
  surfaced as `409 appointment_conflict` (`AppointmentService` maps the
  constraint name from `IntegrityError.orig.diag.constraint_name` — never
  by parsing the error message string). Verified end-to-end in
  `tests/integration/test_appointment_concurrency.py` using two genuinely
  independent database connections/threads, not a simulated sequential
  call. Both exclusion constraints are scoped to `status IN ('scheduled',
  'confirmed')` — a cancelled appointment never blocks a new booking.
* **Half-open interval convention** (`[start, end)`) is used everywhere
  (appointments, blocks, availability) — two bookings that only touch at a
  boundary never conflict.

### Buffer strategy — matches task.md exactly, not a gap

A service type's `buffer_before_minutes`/`buffer_after_minutes` are
absorbed into the availability engine's schedule-window shrinking during
slot generation (the free interval is shrunk by the buffer before bookable
slots are computed) — they are **not** persisted as separate
`occupied_starts_at`/`occupied_ends_at` columns and are **not** part of the
database exclusion constraint's own range. This was re-verified against
`tasks/current/task.md`'s own "Domain model" section during the MED-005
repair round: the task explicitly specifies the exclusion constraint over
`tstzrange(starts_at, ends_at, '[)')` — the bare appointment interval, not
a buffer-expanded one — and separately, explicitly documents that
`CalendarBlock`-vs-`Appointment` conflict prevention (which buffers
effectively extend) is service-layer only "not by a database constraint
spanning both tables," with the residual race window called out as an
explicit, accepted, product-acceptable limitation for this foundation
slice. The current implementation is therefore a direct match for the
governing spec, not a compromise against it — buffer-vs-buffer protection
between two *concurrent* bookings is enforced only at the service-layer
pre-check, by design.

### Optimistic locking

Every appointment mutation (reschedule, cancel, confirm, complete, no-show,
metadata update) is a single `UPDATE ... WHERE id = :id AND tenant_id =
:tenant_id AND version = :expected_version ... RETURNING *`
(`AppointmentRepository.update_with_version`) — the database, not a prior
Python-level read, is the sole arbiter of whether the caller's expected
version still matches. A zero-row result is always `409 stale_version`.
This is the first use of this pattern in the codebase, chosen over `SELECT
... FOR UPDATE` specifically to avoid holding a row lock across a
potentially slow availability recheck.

### Status transitions and authorization

* Allowed transitions: `scheduled → {confirmed, cancelled, completed,
  no_show}`, `confirmed → {cancelled, completed, no_show}`; `cancelled`/
  `completed`/`no_show` are terminal. Any other requested transition is
  `409 invalid_status_transition`.
* `cancel` is **idempotent** — cancelling an already-cancelled appointment
  returns its current state rather than re-raising or re-auditing, so a
  retried request (e.g. after a lost response) never surfaces as an error.
* `complete`/`no_show` allow a **self-scoped bypass**: any active member
  may always act on an appointment where they are the `provider_user_id`,
  regardless of role — "provider" is a fact, not a permission grant.
  `cancel`/`confirm`/`reschedule` have no such bypass and always require a
  `CALENDAR_WRITE_ROLES` role, even for the appointment's own provider.
  `update_metadata` (patient contact snapshot, notes, room) likewise has
  **no** self-scoped bypass — being the appointment's own provider does
  not, by itself, grant the right to edit its patient contact/notes/room
  fields; every caller, including the provider, needs
  `CALENDAR_WRITE_ROLES` (fixed in the MED-005 repair round; an earlier
  version incorrectly extended the complete/no-show bypass to metadata
  updates too).
* **Role matrix** (`backend/app/core/authorization.py`): `CALENDAR_READ_ROLES`
  = owner/manager/operator/auditor; `CALENDAR_WRITE_ROLES` =
  owner/manager/operator; `CALENDAR_CONFIG_ROLES` (rooms, service types,
  schedules, calendar blocks) = owner/manager; `CALENDAR_OVERRIDE_ROLES`
  (booking outside the computed availability, with a required
  `override_reason`) = owner/manager; `CALENDAR_CONTACT_VISIBLE_ROLES`
  (patient phone/email snapshot) = owner/manager/operator — `AUDITOR` may
  read the calendar and always sees `patient_display_name` (redaction is
  field-level, not row-level, so the appointment stays identifiable in a
  calendar view) but never the patient phone/email snapshot
  (`app.api.appointments._serialize` returns a redacted
  `AppointmentSummaryRead` instead of `AppointmentRead`), and
  `CONTENT_EDITOR` has no calendar permission at all in this slice.
  `_serialize` is keyed **only** on the caller's role — never on whether
  the caller happens to be the appointment's own provider — and is the
  single place every appointment response (read, list, create, metadata
  update, reschedule, every lifecycle action) is built; no route calls
  `AppointmentRead.model_validate(...)` directly (fixed in a second
  MED-005 repair round: an earlier version had a self-provider bypass in
  `_serialize` that granted full contact visibility regardless of role,
  and several lifecycle-action routes bypassed `_serialize` entirely by
  calling `AppointmentRead.model_validate(...)` directly).
* **Availability self-scope**: `GET /api/v1/availability` is not gated by
  `CALENDAR_READ_ROLES` alone — per task.md's authorization matrix ("View
  own calendar (self as provider) | any active member (self-scoped
  only)"), every active member may query availability for
  `provider_id == their own user id` regardless of role;
  `require_calendar_read_or_self` (`app.core.authorization`) is the
  authoritative check, called from `AvailabilityService.get_availability`
  itself, not just an API-layer dependency. Querying another provider's
  availability still requires `CALENDAR_READ_ROLES`. (Fixed in the MED-005
  repair round; an earlier version rejected every non-`CALENDAR_READ_ROLES`
  caller outright, including for their own availability.)
* **Conflict error codes** (`app.core.errors.CalendarConflictError`):
  `appointment_conflict` (an existing blocking appointment overlaps —
  provider or room), `provider_unavailable` (a `ProviderSchedule` rule
  exists for that day, but the requested window falls outside its hours or
  inside a break), `room_unavailable` (room inactive, or a room-scoped
  `CalendarBlock` overlaps), `blocked_period` (a provider-scoped
  `CalendarBlock` overlaps), `outside_schedule` (no `ProviderSchedule` rule
  matches that day at all), `invalid_status_transition`, `stale_version` —
  the full 7-code set task.md's "Error contract" section explicitly
  requires (fixed in the MED-005 repair round; an earlier version
  collapsed all four availability-related reasons into a single
  `outside_schedule` code). `AvailabilityService.diagnose_unavailable_reason`
  classifies the specific reason, in a fixed priority order (existing
  appointment overlap → room state/block → provider block → no schedule
  at all → schedule exists but doesn't cover this window), only ever
  called after `is_interval_free` has already returned `False`. Sequential
  double-bookings that the service-layer pre-check catches now report
  `appointment_conflict` too — the same code the DB exclusion constraint
  itself would raise for a genuine concurrent race — so a caller sees one
  consistent code regardless of which layer caught it. Inactive-resource
  (other than room) and not-found conditions use the existing plain
  `ConflictError`/`NotFoundError` without a machine code.

### DST handling

`app.core.scheduling_time.combine_local` resolves a local
date+time-of-day into a UTC instant for a given `ProviderSchedule`/
`ScheduleBreak` row. A nonexistent local time (the spring-forward gap) is
**skipped** for that occurrence, never silently shifted forward or
backward; an ambiguous local time (the fall-back overlap) resolves to its
**first** occurrence (`fold=0`). Both are documented, deliberate policy
choices, covered by `tests/unit/test_scheduling_time.py` using the actual
2026 EU DST transition dates.

### API surface

`GET/POST /api/v1/rooms`, `GET/POST /api/v1/appointment-service-types`,
`GET/POST /api/v1/provider-schedules` (+ breaks), `GET/POST
/api/v1/calendar-blocks`, `GET /api/v1/availability`, and the full
`/api/v1/appointments` lifecycle (create, get, list, patch-metadata,
reschedule, cancel, confirm, complete, no-show) — every mutating route
behind `Depends(require_csrf)`, business-logic-in-routes forbidden (routes
call a service method and serialize its return value only), and
transaction ownership stays in the service layer throughout (repositories
`flush()`, never `commit()`).

`PATCH /api/v1/appointments/{id}` (`AppointmentMetadataUpdate`) distinguishes
an **omitted** field (left unchanged) from an **explicit `null`** (cleared)
via Pydantic's `model_fields_set` — never a plain `if value is not None`
filter, which cannot tell the two apart and would make an already-set
`patient_phone`/`patient_email`/`notes`/`room_id` impossible to clear
(fixed in a second MED-005 repair round). `room_id` is an accepted
metadata field (task.md scopes this PATCH to "patient contact snapshot,
notes, room") and goes through the same tenant/active-room/availability
validation as create/reschedule — never a bare column write; `patient_display_name`
cannot be cleared (schema-level `422`) since it is not nullable on the
model.

### Frontend

`frontend/app/calendar` — day **and week** view calendar with a view-mode
toggle (date navigation, provider/room/status filters, appointment cards
with role/self-scoped-aware action buttons, an inline creation form with an
availability-driven slot picker, and an inline reschedule panel), plus
`frontend/app/settings/{rooms,service-types,schedules,blocks}` for the
configuration CRUD screens. Week view uses a Monday-based week start
(`app/lib/calendar-time.ts`'s `startOfWeek`/`weekDates`/
`localWeekBoundsUtc` — Bulgaria and most of Europe start the week on
Monday, not Sunday like `Date.getDay()`/`Intl` default to), fetches the
full 7-day range in one request, and groups results client-side by local
calendar date (`localDateString`) — never 7 separate day requests. Every
timestamp is parsed/rendered through `app/lib/calendar-time.ts` (offset-aware
ISO parsing, tenant-timezone-aware `Intl` formatting, DST-safe local-day/
local-week UTC bounds) — never a naive `new Date("...")` string-splice.
Action visibility mirrors the backend role/self-scoped matrix in
`app/lib/appointment-policy.ts` as a usability convenience only; the
backend independently re-derives and
enforces every rule regardless of what the UI shows or hides. `GET
/api/v1/clinic` now additionally returns the tenant's `timezone` (added in
this slice) so the frontend never has to guess it.

### Known limitations

* No practitioner-records module — a "provider" is any active
  `TenantMembership` referenced by a schedule/appointment row; there is no
  dedicated profile, specialty, or display-name concept, so the UI shows a
  raw user id wherever a provider is referenced (consistent with the
  existing MED-003 staff roster, which has the same limitation).
* No patient-record storage beyond the per-appointment contact snapshot —
  there is no searchable patient directory, and nothing in this slice
  claims otherwise.
* No notification delivery (appointment confirmations/reminders) — matches
  the "no email delivery exists" limitation already documented for MED-004.
* Buffer-vs-buffer and `CalendarBlock`-vs-`Appointment` race protection is
  service-layer only, not database-enforced (see "Buffer strategy" above)
  — this matches task.md's own explicit "Concurrency strategy" section
  exactly (re-verified during the MED-005 repair round), not a deviation
  from it.
* Provider-schedule overlap rejection is service-layer only, not a database
  exclusion constraint (see "Domain model" above) — a documented scope
  reduction.

## Deterministic business services

**[implemented — MED-005, partial]** `app.services.appointment_service`,
`schedule_service`, `availability_service`, `room_service`,
`service_type_service`, and `calendar_block_service` are the first
deterministic, testable service functions with explicit inputs/outputs (not
model inference) in this codebase — see "Appointments and calendar" above.
The `policy-engine` module for more general business-policy evaluation
remains **[planned]**.

## AI boundary

**[planned]** Any LLM component in this platform is restricted to **language
understanding and response composition** — reading a user's message, and
drafting a reply or a summary. An LLM:

* cannot directly write to the database;
* cannot directly execute a booking, cancellation, or schedule change;
* cannot bypass server-side authorization or tenant isolation.

Any AI-suggested action must be translated into a call against the
deterministic service layer above, which independently re-validates
authorization, tenant scope, and business rules before doing anything. This
keeps the system auditable and gives a clean human-handover point when the AI
is uncertain or the user asks for a human.

## Auditability & human handover

**[planned]** Every state-changing action (once implemented) is expected to
produce an audit log entry (`audit` module) recording who/what/when at the
service layer, not the presentation layer. Conversational flows must support
handing a conversation to a human operator without losing context.

## Implemented components

* `backend/` — FastAPI application factory, `/health` and `/ready` endpoints,
  environment-based settings, SQLAlchemy session scaffolding, Alembic
  initialization, Redis and Qdrant client scaffolding, structured logging,
  centralized error handling.
* `backend/` tenancy foundation — `Tenant`/`TenantMembership` models, request-level
  tenant context resolution, tenant-scoped repository/service/authorization
  pattern, background-job tenant context contract, and a structured-logging
  audit adapter. See "Multi-tenancy" above.
* `backend/` clinic and staff administration (MED-003) — clinic (tenant)
  settings view/edit and staff (membership) administration with the role
  matrix and final-owner invariant described in "Clinic and staff
  administration" above.
* `backend/` production authentication (MED-004) — `UserAccount`/
  `AuthSession`/`OneTimeToken` models, Argon2id password hashing,
  server-side opaque sessions, session-tied CSRF double-submit,
  Redis-backed login rate limiting, and the `/api/v1/auth/*` endpoints
  described in "Authentication and user identity" above. `get_tenant_context`
  now resolves a real session before falling back to the development
  identity provider.
* `backend/` appointments and calendar foundation (MED-005) — rooms,
  service types, provider schedules (+ breaks), calendar blocks, the
  dynamic availability engine, and the full appointment lifecycle
  (optimistic locking, dual-layer double-booking prevention, DST-aware
  scheduling) described in "Appointments and calendar" above. No
  dedicated practitioner-records, billing, or notification module is
  implemented on top of it yet.
* `frontend/` — Next.js App Router shell with a landing page that displays
  backend health status, `/login`, `/forgot-password`, `/reset-password`,
  `/select-clinic`, and `/invitations/accept` for the authentication flow,
  `/settings/clinic`, `/settings/staff`, and `/settings/security`
  (authenticated change-password), `/calendar` and
  `/settings/{rooms,service-types,schedules,blocks}` for the MED-005
  scheduling UI — all session-authenticated (`app/lib/api.ts`), with the
  development identity picker (and its `/dev/identity` entry point)
  retained as a local-testing convenience only and never rendered/reachable
  in a production build.
* `infra/` — Docker Compose definitions for Postgres, Redis, Qdrant, backend,
  frontend.

## Planned modules

| Module         | Purpose                                                        |
|----------------|------------------------------------------------------------------|
| authentication | User/staff login, session management (implemented — see "Authentication and user identity"); MFA/SSO still planned |
| clinics        | Clinic settings + staff/membership administration (implemented — see "Clinic and staff administration") |
| practitioners  | Clinical practitioner records/profiles (distinct from the "provider" fact used by MED-005 scheduling — see "Appointments and calendar" "Known limitations") |
| services       | Billable clinical services offered by a clinic (implemented as non-billable `AppointmentServiceType` configuration — see "Appointments and calendar"; no price/currency/billing field exists) |
| schedules      | Provider availability and working hours (implemented — see "Appointments and calendar") |
| appointments   | Booking, rescheduling, cancellation (implemented — see "Appointments and calendar") |
| knowledge base | Clinic-specific FAQ / policy content for AI-assisted responses    |
| conversations  | Patient/staff conversational sessions, human handover              |
| policy engine  | Deterministic evaluation of business/booking policies (the appointment/schedule services above are the first deterministic service functions — see "Deterministic business services") |
| notifications  | Email/SMS/push delivery for appointment and account events         |
| audit          | Immutable, persistent audit log (an interim structured-logging adapter exists — see "Audit-event flow" above) |
| analytics      | Aggregated, tenant-scoped operational reporting                    |

No module marked purely "planned" above has been implemented. Do not add
business logic to `backend/` until a module is explicitly scoped and
approved as separate work.
