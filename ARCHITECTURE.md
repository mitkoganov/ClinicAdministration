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
  `user_id` (a bare UUID column with **no** foreign key — no `User` table
  exists yet; this is the documented placeholder identifier the future
  authentication module will replace) to a `tenant_id`, with a `role`
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
  audit adapter. See "Multi-tenancy" above. No business module (clinics,
  practitioners, appointments, ...) is implemented on top of it yet.
* `frontend/` — Next.js App Router shell with a landing page that displays
  backend health status.
* `infra/` — Docker Compose definitions for Postgres, Redis, Qdrant, backend,
  frontend.

## Planned modules

| Module         | Purpose                                                        |
|----------------|------------------------------------------------------------------|
| authentication | User/staff login, session management, MFA (future)               |
| clinics        | Individual clinic records under a tenant                          |
| practitioners  | Clinic staff / practitioners and their roles                      |
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
