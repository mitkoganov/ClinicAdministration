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
  in the URL a user follows (see "Known limitations" on delivery).

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
  pair. **Logout** (`POST /api/v1/auth/logout`) is idempotent — a
  missing/already-invalid session still clears cookies and returns
  success, never revealing which condition applied — and revokes the
  session server-side (`revoked_at`), not just client-side cookie
  clearing.
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
`logout`/`select-clinic`/`change-password` and to the MED-003 mutating
clinic/staff routes, not to `login`/`password-reset`/`invitation-accept`,
which have no session yet to tie a token to.

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
headers when no valid session exists. **A valid session always wins**; a
dev header can never override or shadow one. This makes every existing
MED-002/MED-003 tenant-scoped route session-aware automatically, without
editing those route files.

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
| `POST /logout` | optional | yes | Idempotent session revocation |
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

## Deterministic business services

**[planned]** All business actions (creating an appointment, changing a
schedule, applying a policy) are performed by deterministic, testable service
functions in the backend — plain code with explicit inputs/outputs, not model
inference. This applies once the `appointments`, `schedules`, and
`policy-engine` modules exist.

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
  administration" above. No other business module (practitioners,
  appointments, ...) is implemented on top of it yet.
* `backend/` production authentication (MED-004) — `UserAccount`/
  `AuthSession`/`OneTimeToken` models, Argon2id password hashing,
  server-side opaque sessions, session-tied CSRF double-submit,
  Redis-backed login rate limiting, and the `/api/v1/auth/*` endpoints
  described in "Authentication and user identity" above. `get_tenant_context`
  now resolves a real session before falling back to the development
  identity provider.
* `frontend/` — Next.js App Router shell with a landing page that displays
  backend health status, `/login`, `/forgot-password`, `/reset-password`,
  and `/invitations/accept` for the new authentication flow, plus
  `/settings/clinic` and `/settings/staff` — now session-authenticated
  (`app/lib/api.ts`), with the development identity picker retained
  as a local-testing convenience only and never rendered in a production
  build.
* `infra/` — Docker Compose definitions for Postgres, Redis, Qdrant, backend,
  frontend.

## Planned modules

| Module         | Purpose                                                        |
|----------------|------------------------------------------------------------------|
| authentication | User/staff login, session management (implemented — see "Authentication and user identity"); MFA/SSO still planned |
| clinics        | Clinic settings + staff/membership administration (implemented — see "Clinic and staff administration") |
| practitioners  | Clinical practitioner records and scheduling roles (distinct from staff/membership administration above) |
| services       | Billable clinical services offered by a clinic                    |
| schedules      | Practitioner availability and working hours                       |
| appointments   | Booking, rescheduling, cancellation (deterministic service layer) |
| knowledge base | Clinic-specific FAQ / policy content for AI-assisted responses    |
| conversations  | Patient/staff conversational sessions, human handover              |
| policy engine  | Deterministic evaluation of business/booking policies               |
| notifications  | Email/SMS/push delivery for appointment and account events         |
| audit          | Immutable, persistent audit log (an interim structured-logging adapter exists — see "Audit-event flow" above) |
| analytics      | Aggregated, tenant-scoped operational reporting                    |

No module above has been implemented. Do not add business logic to `backend/`
until a module is explicitly scoped and approved as separate work.
