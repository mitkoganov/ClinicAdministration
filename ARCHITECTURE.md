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

**[planned]** Tenancy model: a clinic (or clinic group) is a tenant. All
tenant-scoped tables carry a `tenant_id`. Tenant isolation is enforced
**server-side** in the data access layer — never trusted from client input,
never inferred solely from a URL parameter. The current foundation has no
tenant model yet; this is the first planned module.

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
* `frontend/` — Next.js App Router shell with a landing page that displays
  backend health status.
* `infra/` — Docker Compose definitions for Postgres, Redis, Qdrant, backend,
  frontend.

## Planned modules

| Module         | Purpose                                                        |
|----------------|------------------------------------------------------------------|
| authentication | User/staff login, session management, MFA (future)               |
| tenants        | Tenant (clinic group) registration and configuration              |
| clinics        | Individual clinic records under a tenant                          |
| practitioners  | Clinic staff / practitioners and their roles                      |
| services       | Billable clinical services offered by a clinic                    |
| schedules      | Practitioner availability and working hours                       |
| appointments   | Booking, rescheduling, cancellation (deterministic service layer) |
| knowledge base | Clinic-specific FAQ / policy content for AI-assisted responses    |
| conversations  | Patient/staff conversational sessions, human handover              |
| policy engine  | Deterministic evaluation of business/booking policies               |
| notifications  | Email/SMS/push delivery for appointment and account events         |
| audit          | Immutable audit log of state-changing actions                      |
| analytics      | Aggregated, tenant-scoped operational reporting                    |

No module above has been implemented. Do not add business logic to `backend/`
until a module is explicitly scoped and approved as separate work.
