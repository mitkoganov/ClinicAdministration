# MED-003 — Clinic and Staff Administration

## Preconditions

This task builds on the approved MED-002 multi-tenant foundation
(`Tenant`/`TenantMembership` models, request-level tenant context
resolution, tenant-scoped repository/service/authorization pattern,
background-job tenant context, audit adapter, and the disposable-test-
database safety infrastructure). PR #1 (MED-002) must be merged into
`master` before this task starts; implementation happens on
`feature/med-003-clinic-staff-administration`.

---

## Objective

Provide secure clinic and staff administration using the tenant,
membership, authorization, audit, and database-isolation foundations
delivered by MED-002.

This task produces a functional vertical slice:

* clinic administrators can view and manage their own clinic;
* authorized users can view and manage staff memberships;
* users cannot access or infer data belonging to another clinic;
* the frontend provides a usable administration interface;
* all state-changing operations are audited.

---

## Domain terminology

The existing `Tenant` model is the persistence and security boundary. In
user-facing API schemas and frontend copy, the term "clinic" is used
instead — a clinic maps one-to-one to a tenant. The database tenant model
and MED-002 migration history are unchanged.

---

## Roles

Preserves the existing MED-002 membership roles and their meaning.

* **Owner** — view/edit clinic settings; view/invite/change-role/
  activate-deactivate/remove staff. Must not be removable or deactivated
  if doing so would leave the clinic without an active owner.
* **Manager** — view clinic settings (cannot edit); view staff; invite
  only `operator`/`auditor`; activate/deactivate non-owner memberships;
  remove `operator`/`auditor` memberships. Must never create, promote to,
  demote, remove, or otherwise mutate an `owner` membership, and must
  never grant `owner`.
* **Operator** — views their own active clinic context only; no staff
  visibility or mutation in this slice (see "Known limitations").
* **Auditor** — read-only: may view clinic settings and the membership
  list; must not mutate clinic or membership data.

Authorization is enforced server-side, inside the service layer (not only
the API dependency layer) for every rule above, including a rejection of
self-elevation (a caller can never use these endpoints to increase their
own role's privilege tier — voluntary self-demotion is allowed).

---

## Backend requirements

### Clinic API

* `GET /api/v1/clinic` — returns the active clinic represented by the
  current tenant context (no arbitrary tenant ID accepted from the
  client) plus the caller's own role.
* `PATCH /api/v1/clinic` — allows only the display name to change.
  Request schema uses an explicit allowlist (`extra="forbid"`) so tenant
  primary key, ownership boundary, internal status, audit metadata,
  timestamps, and slug can never be set via this endpoint.

### Staff (membership) API

* `GET /api/v1/clinic/staff` — deterministic sort, pagination, optional
  role/status filters, all additionally tenant-scoped.
* `POST /api/v1/clinic/staff` — creates a membership for an existing
  development/test `user_id` (see "Known limitations" — this is
  provisioning, not an email-invitation system). Rejects a duplicate
  membership for the same `(tenant_id, user_id)` with `409 Conflict`.
* `PATCH /api/v1/clinic/staff/{membership_id}` — role and/or status
  change, subject to the role matrix, self-elevation rejection, and the
  final-owner invariant below.
* `DELETE /api/v1/clinic/staff/{membership_id}` — soft-deactivates the
  membership (sets `status=inactive`); this is the documented membership
  lifecycle already established by MED-002, not a physical row delete.

Every membership lookup by `membership_id` is a single tenant-scoped
query — a cross-tenant or nonexistent `membership_id` returns the
identical generic `404`, never a distinguishing response.

### Final-owner protection

A clinic can never end up with zero active owners. Before demoting,
deactivating, or removing an `owner` membership, the service row-locks
(`SELECT ... FOR UPDATE`) every currently-active owner membership in that
tenant and rejects with `409 Conflict` if the target is the only one —
closing the race where two concurrent requests each individually see
"not the last owner" a moment before both commit.

### Service and repository boundaries

Follows the established `API → service → repository → database`
architecture: services own business authorization and invariants and
independently re-derive them from freshly-read rows (never trusting a
client-supplied role/status for anyone other than the acting caller);
repositories are tenant-scoped in a single query per method, with no
generic method that allows a caller to omit tenant scope; tenant context
comes only from the server-side resolver.

### Audit requirements

Emits audit events for: clinic settings updated; membership created;
membership role changed; membership activated; membership deactivated;
membership removed; and rejected high-risk administration attempts
(insufficient role, duplicate membership, final-owner violation).
Success events are emitted only after the database operation has
committed — never before, and never if the commit fails. Audit payloads
contain identifiers and safe metadata only — no passwords, tokens,
secrets, full request bodies, or sensitive personal/medical data.

### API error behavior

Insufficient role → generic `403`. Nonexistent or cross-tenant
membership → identical generic `404`. Final-owner invariant violation →
`409 Conflict`. Duplicate membership → `409 Conflict`. Malformed role or
status → schema validation error (`422`).

### Database and migration

No new tables: "clinic" is the existing `Tenant` model; "staff" is the
existing `TenantMembership` model. The migration for this task adds only
two composite indexes on `tenant_memberships` (`tenant_id, role` and
`tenant_id, status`) to support tenant-scoped, filtered, paginated staff
listing — the final-active-owner invariant is transactional service logic
(row locking), not a database constraint, since it depends on runtime
state across multiple rows.

Migration validation: upgrade from an empty database to head, downgrade
of this task's revision, re-upgrade to head — executed only against the
disposable test database, never the normal development database.

---

## Frontend requirements

Two new pages under the existing Next.js App Router shell:

* `/settings/clinic` — clinic name/slug/status/current-role display, with
  an edit form for permitted users (owner only); loading, empty/error,
  validation, disabled-submitting, and success states.
* `/settings/staff` — a paginated, role/status-filterable staff table
  with add/role-change/activate-deactivate/remove actions and
  confirmation dialogs for deactivation, removal, and privilege-sensitive
  role changes. The UI hides unavailable actions per the caller's role for
  usability; the backend independently enforces every rule regardless of
  what the UI offers.

Since no authentication UI exists yet, both pages use a client-side,
localStorage-backed development identity picker (`X-Dev-User-Id`/
`X-Tenant-Id`) — never a security boundary, purely a local-testing
convenience matching the backend's existing dev-identity mechanism.

---

## Tests

**Unit tests** cover: clinic update authorization; membership role-change
authorization; manager restrictions (cannot grant/mutate owner); self-
elevation rejection; the final-owner invariant; duplicate-membership
handling; active/inactive membership transitions; audit event timing
(commit-before-success-audit, no success audit on failed commit); schema
allowlisting; service fail-closed behavior. Tests that exercise the real
Postgres database (via `db_session`/`tenancy`) live under
`backend/tests/integration/`, marked `pytest.mark.integration`, regardless
of whether they test service-layer logic — directory placement reflects
actual dependency on the database, not the layer under test.

**Integration tests** cover at least: owner view/update own clinic;
manager view-but-not-edit; operator cannot update clinic settings;
auditor read-only; owner staff listing; cross-tenant staff never
appearing; identical cross-tenant/nonexistent membership 404; owner
adding an allowed membership; duplicate-membership rejection; manager
cannot grant/mutate owner; operator/auditor cannot manage staff;
final-owner cannot be demoted/deactivated/removed; role change audited
only after commit; failed commit emits no success audit; tenant/
membership deactivation respected immediately; pagination and filters
remain tenant-scoped.

**Frontend**: no test framework exists in this repository yet, and this
task does not introduce one solely for itself — verification relies on
`lint`, `typecheck`, and `production build`, plus a manual smoke check of
both pages against a live backend.

---

## Quality gates

Run the official workflow (`.ai-workflow/scripts/run-tests.ps1`) — ruff,
ruff format, mypy, unit pytest, integration pytest, frontend lint/
typecheck/build, Docker Compose config validation (including the test
profile), backend image build, `git diff --check`. Run migration
upgrade/downgrade/re-upgrade against the disposable test database and
save the standard evidence report. Run `.ai-workflow/scripts/run-review.ps1`
and stop for human approval — do not automatically repair Codex findings.

---

## Out of scope

Patient records, appointments, billing, clinical workflows, production
email-invitation delivery, and a production authentication redesign are
explicitly not part of this task. (Practitioners/clinical-role records
remain a separate future module, distinct from the staff/membership
administration implemented here.)

---

## Completion criteria

MED-003 is complete only when: clinic settings are securely available;
staff memberships can be administered according to the role matrix;
final-owner protection is enforced; cross-tenant membership access is
impossible; audit events are transaction-safe; frontend pages build and
function; migration validation passes; all quality gates pass; Codex
returns `APPROVED`; no generated reports are committed; nothing is
pushed, merged, or deployed automatically.
