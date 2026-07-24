# MED-005 — Appointments and Calendar Foundation

**Status:** READY

## Preconditions

This task builds on the approved and merged MED-002 (multi-tenant
foundation), MED-003 (clinic and staff administration), and MED-004
(production authentication and user identity) work on `master`.
Implementation happens on `feature/med-005-appointments-calendar-foundation`.

## Current architecture assumptions (read before implementing)

These are facts about the *existing* codebase this task must build on
top of, not aspirational design — verify them again at implementation
time, since this section is a snapshot, not a substitute for reading the
actual code.

* There is no separate `clinics` table. "Clinic" is the documented
  user-facing term for the existing `Tenant` model
  (`backend/app/models/tenant.py`) — a clinic maps one-to-one to a
  tenant. Every new tenant-scoped table in this task uses a `tenant_id`
  column (`ForeignKey("tenants.id")`), never `clinic_id` — this matches
  `TenantMembership`/`TenantScopedRecord` exactly.
* `Tenant` currently has no timezone field. This task adds one (see
  "Timezone policy" below) via a new migration column, not by editing
  the released MED-002 migration.
* Staff/membership administration (MED-003) is `TenantMembership`, not a
  separate `Staff` model. There is still no dedicated `Practitioner`
  model — ARCHITECTURE.md's "Planned modules" table lists
  `practitioners` as a distinct, still-unimplemented future module. This
  task deliberately does **not** implement it: a "provider" in MED-005
  is simply an existing tenant member (a `TenantMembership` row, any
  role) who has at least one `ProviderSchedule` row. Do not add a
  `Practitioner` table in this task.
* `TenantMembership.user_id` has no foreign key to `UserAccount.id`
  (deliberately — MED-002 predates MED-004's `UserAccount`, and released
  migrations are never edited). Every new "provider" reference in this
  task follows the same precedent: a bare `provider_user_id: uuid.UUID`
  column with **no** FK constraint, validated at the service layer
  (active `TenantMembership` for that `tenant_id` + `user_id`), not by
  the database.
* Every model: UUID primary key (`Uuid(as_uuid=True)`, default
  `uuid.uuid4`), `Base` from `app.db.base`, `DateTime(timezone=True)`
  timestamps (`server_default=func.now()`, `onupdate=func.now()` for
  `updated_at`), lifecycle via a two-value `status` `StrEnum`
  (`ACTIVE`/`INACTIVE`) rather than a boolean flag or physical delete,
  `SAEnum(..., native_enum=False, values_callable=...)` for every enum
  column (plain `VARCHAR` + `CHECK`, never a native Postgres `ENUM`
  type), explicit stable constraint/index names
  (`uq_..., ix_..., ck_..., fk_..., pk_...`).
* Repositories: `<Entity>Repository(db: Session)`, every method takes
  `tenant_id` explicitly and scopes the query in one shot (never
  lookup-then-compare), `flush()` only — repositories never `commit()`.
* Services own the transaction (`self._db.commit()`), commit **before**
  emitting a `SUCCESS` audit event, re-check authorization themselves via
  `app.core.authorization.require_role` (the authoritative boundary,
  independent of whatever the API layer's `Depends(require_roles(...))`
  already rejected), and audit rejected attempts too
  (`AuditOutcome.REJECTED`).
* API layer: `APIRouter(prefix="/api/v1/<module>", tags=["<module>"])`;
  mutating routes add `dependencies=[Depends(require_csrf)]`; routes that
  need the authoritative tenant/role context use
  `Depends(get_tenant_context)` (service re-checks the role) or
  `Depends(require_roles(*ROLES))` for cheap early-rejection on read-only
  routes (rejected reads are not audited, so no downside there).
* Roles (`backend/app/models/membership.py`): `MembershipRole` =
  `OWNER, MANAGER, OPERATOR, CONTENT_EDITOR, AUDITOR`. There is no
  dedicated "provider"/"reception" role — see the authorization matrix
  below for how this task maps real roles onto calendar operations. Do
  not add a new `MembershipRole` value in this task.
* No PostgreSQL-specific feature (GiST, `EXCLUDE` constraint,
  `tstzrange`, `btree_gist`) is used anywhere in this codebase yet. This
  task introduces the **first** use of that pattern — there is no
  existing precedent to copy; the migration must enable the extension
  itself (see "Database and migration requirements").
* Postgres version in use (dev and disposable test service): `postgres:17-alpine`
  (`docker-compose.yml`) — supports `btree_gist` natively.
* Audit: `app.core.audit.AuditEvent`/`AuditOutcome`/`emit_audit_event` —
  a structured-logging sink on the `"audit"` logger, no persistent audit
  store yet. Never place secrets, raw tokens, or excessive PII in an
  event.
* Frontend: flat `app/<route>/page.tsx` structure, no calendar library,
  no CSS framework (inline `style={{...}}`), `apiFetch<T>()` from
  `frontend/app/lib/api.ts` (CSRF-aware, `credentials: "include"`,
  throws `ApiError`), authenticated shell + nav in
  `frontend/app/settings/layout.tsx`.

## Objective

Implement a tenant-scoped **foundation** for clinic scheduling and
appointment booking: rooms, service types, provider working hours,
recurring breaks, one-off blocked periods, a dynamic availability
(slot) engine, and the appointment lifecycle itself (create, reschedule,
cancel, confirm, complete, no-show) — with database-level double-booking
protection, full tenant isolation, server-side authorization, audit
events, and a foundation calendar UI.

The functional vertical slice includes:

* `ClinicRoom`, `AppointmentServiceType`, `ProviderSchedule`,
  `ScheduleBreak`, `CalendarBlock`, `Appointment` models and their
  Alembic migration;
* a tenant `timezone` column;
* repository + service layer for each entity;
* a dynamically-computed (non-materialized) availability/slot engine;
* PostgreSQL-level overlap protection for active appointments (provider
  and room);
* the full appointment lifecycle with explicit status transitions;
* a role-based authorization matrix built on the existing
  `MembershipRole` enum;
* audit events for every mutation and every rejected attempt;
* versioned, tenant-scoped API endpoints;
* a foundation calendar UI (day/week view, creation, reschedule,
  cancellation) and admin settings pages for rooms/service
  types/schedules/blocks;
* comprehensive backend and frontend tests;
* documentation updates.

## Non-goals (explicit scope exclusions)

The following are **not** part of MED-005 and must not be implemented,
scaffolded, or implied as existing:

* full patient registry (deferred to a future MED-006-style task);
* electronic medical records, diagnoses, prescriptions, lab results,
  clinical encounter notes;
* billing, insurance, payment processing, service pricing fields;
* email delivery, SMS delivery, reminder scheduling;
* telemedicine;
* external calendar sync (Google Calendar, Outlook, iCal export/import);
* recurring appointment series (a single appointment is a single
  interval — no repeat-booking UI or model);
* waitlists;
* equipment/resource inventory beyond a room reference;
* multi-location appointments spanning more than one tenant;
* public/anonymous self-booking;
* chatbot/AI booking integration;
* a dedicated `Practitioner` model (see "Current architecture
  assumptions");
* production deployment.

Appointments reference only a minimal patient **contact snapshot**
(display name + optional phone/email) captured on the appointment row
itself — never a `patients` table or foreign key. A future patient
registry task may later normalize this into a real relationship; MED-005
must not block that by, e.g., making the snapshot fields immutable
identifiers.

---

## Domain model

All new tables are tenant-scoped (`tenant_id`, `ForeignKey("tenants.id")`,
`nullable=False`, indexed). All enums follow the project's
`SAEnum(..., native_enum=False, values_callable=lambda c: [m.value for m in c])`
convention. All timestamps are `DateTime(timezone=True)`.

### Tenant timezone (extends the existing `Tenant` model)

Add a new column to `Tenant` (new migration, not an edit to MED-002's
migration):

* `timezone: str` — an IANA timezone name (e.g. `"Europe/Sofia"`),
  `String(64)`, `nullable=False`, `server_default="Europe/Sofia"`.
* Validate on write (a `@validates("timezone")` hook, mirroring the
  existing `slug` pattern) that the value is a real IANA name resolvable
  via Python's `zoneinfo.ZoneInfo` — reject anything else with a clear
  `ValueError` before it reaches the database. A `CheckConstraint` at the
  DB level cannot validate against the full IANA database, so this is
  ORM-layer validation only; document that explicitly rather than
  overclaiming DB-level enforcement.
* `Europe/Sofia` is a sensible default for existing tenants created
  before this column existed, but must never be hardcoded inside any
  service/availability logic — every calendar computation reads
  `tenant.timezone`.

### ClinicRoom

Table: `clinic_rooms`.

* `id: uuid.UUID` (PK);
* `tenant_id: uuid.UUID` (FK `tenants.id`, indexed);
* `name: str` (`String(200)`, not empty);
* `code: str` (`String(50)`) — unique **within a tenant**
  (`UniqueConstraint("tenant_id", "code", name="uq_clinic_rooms_tenant_code")`);
* `description: str | None` (`Text`, nullable);
* `status: ClinicRoomStatus` (`ACTIVE`/`INACTIVE`, default `ACTIVE`);
* `created_at`, `updated_at`.

Invariants: tenant-scoped everywhere (repository methods take
`tenant_id`); `code` unique per tenant; an `INACTIVE` room cannot be
selected for a **new** appointment/schedule/block (service-layer check);
existing/historical appointments referencing a since-deactivated room
are never deleted or reassigned.

### AppointmentServiceType

Table: `appointment_service_types`.

* `id`, `tenant_id`, `name`, `code` (unique per tenant, same pattern as
  `ClinicRoom`), `description: str | None`;
* `default_duration_minutes: int` (`CheckConstraint(> 0)`, and a sane
  upper bound, e.g. `<= 480` — 8 hours — to reject accidental garbage,
  not a real clinical limit);
* `buffer_before_minutes: int` (`CheckConstraint(>= 0)`, default `0`);
* `buffer_after_minutes: int` (`CheckConstraint(>= 0)`, default `0`);
* `status: ServiceTypeStatus` (`ACTIVE`/`INACTIVE`);
* `created_at`, `updated_at`.

No price/billing field — billing is explicitly out of scope (see
Non-goals). An `INACTIVE` service type cannot be selected for a new
appointment; existing appointments are unaffected.

### ProviderSchedule

Table: `provider_schedules`. Recurring **weekly** availability rule.

* `id`, `tenant_id`;
* `provider_user_id: uuid.UUID` (bare column, **no FK** — see "Current
  architecture assumptions"; indexed together with `tenant_id`);
* `day_of_week: int` (`CheckConstraint("day_of_week >= 0 AND day_of_week <= 6")`,
  `0` = Monday, matching Python's `datetime.weekday()` — document this
  explicitly since ISO weekday and `datetime.weekday()` disagree on
  Sunday);
* `start_time: time` (`Time`, no timezone — local clinic wall-clock, see
  "Timezone policy");
* `end_time: time` (`Time`);
* `effective_from: date`;
* `effective_until: date | None` (nullable — open-ended);
* `room_id: uuid.UUID | None` (FK `clinic_rooms.id`, nullable — a
  provider's default room for this rule; a specific appointment may
  still override the room);
* `status: ProviderScheduleStatus` (`ACTIVE`/`INACTIVE`);
* `created_at`, `updated_at`.

Invariants:

* `start_time < end_time` (`CheckConstraint`);
* `effective_until IS NULL OR effective_until >= effective_from`
  (`CheckConstraint`);
* the provider must have an active `TenantMembership` in `tenant_id` at
  creation time (service-layer check — no FK possible, see above);
* **overlapping active schedule rules for the same
  `(tenant_id, provider_user_id, day_of_week)` with intersecting
  `[effective_from, effective_until)` date ranges and intersecting
  `[start_time, end_time)` time ranges must be rejected at the service
  layer** before insert. A full time-range-and-date-range overlap check
  spans awkwardly across a plain `time` column and a nullable
  `date` range, so this task does **not** require a database exclusion
  constraint for `ProviderSchedule` (unlike `Appointment` — see below);
  document this as a deliberate, narrower guarantee (service-layer
  validated, not DB-enforced) and justify why in the migration/service
  docstring.

### ScheduleBreak

Table: `schedule_breaks`. A recurring break *within* one
`ProviderSchedule` rule (e.g. lunch).

* `id`;
* `schedule_id: uuid.UUID` (FK `provider_schedules.id`, `ondelete="CASCADE"`,
  indexed);
* `start_time: time`;
* `end_time: time`;
* `label: str | None` (`String(100)`, nullable).

No separate `tenant_id` — a break is only ever reached via its parent
schedule, which is itself tenant-scoped; every query joins through
`schedule_id` rather than duplicating `tenant_id` here (document this
decision instead of silently deviating from the "every table has
tenant_id" convention).

Invariants: `start_time < end_time`; the break's
`[start_time, end_time)` must fall **within** the parent schedule's
`[start_time, end_time)`; multiple breaks on the same schedule must not
overlap each other (service-layer check, small in-memory list, no DB
constraint needed at this scale).

### CalendarBlock

Table: `calendar_blocks`. A one-off blocked period (leave, maintenance,
training, room closure, etc.).

* `id`, `tenant_id`;
* `provider_user_id: uuid.UUID | None` (bare, no FK, nullable);
* `room_id: uuid.UUID | None` (FK `clinic_rooms.id`, nullable);
* `starts_at: datetime` (`DateTime(timezone=True)`);
* `ends_at: datetime` (`DateTime(timezone=True)`);
* `reason: str` (`String(300)`, required — never store this as free-text
  medical information; it describes the block, not a patient);
* `block_type: CalendarBlockType` (`StrEnum`: `LEAVE`, `TRAINING`,
  `MAINTENANCE`, `ROOM_CLOSURE`, `PERSONAL`, `OTHER`);
* `created_by_user_id: uuid.UUID` (bare, no FK — same precedent);
* `created_at`, `updated_at`.

Invariants: `starts_at < ends_at`; **at least one of** `provider_id`/
`room_id` must be set (`CheckConstraint`); tenant-scoped everywhere.

### Appointment

Table: `appointments`.

* `id`, `tenant_id`;
* `provider_user_id: uuid.UUID` (bare, no FK, required);
* `room_id: uuid.UUID | None` (FK `clinic_rooms.id`, nullable);
* `service_type_id: uuid.UUID` (FK `appointment_service_types.id`,
  required);
* `starts_at: datetime`, `ends_at: datetime` (both
  `DateTime(timezone=True)`, always stored UTC — see "Timezone policy");
* `status: AppointmentStatus` (`StrEnum`: `SCHEDULED`, `CONFIRMED`,
  `CANCELLED`, `COMPLETED`, `NO_SHOW`; default `SCHEDULED`);
* `patient_display_name: str` (`String(200)`, required);
* `patient_phone: str | None` (`String(32)`, nullable, normalized per
  project convention if one exists for phone numbers — otherwise define
  a minimal E.164-leaning normalization in this task and document it);
* `patient_email: str | None` (`String(320)`, nullable, lowercased/
  trimmed on write, mirroring `UserAccount.normalized_email`'s
  normalization pattern, but this is **not** a login identity — no
  uniqueness constraint);
* `notes: str | None` (`Text`, nullable — operational scheduling notes,
  never clinical/medical notes; document this boundary explicitly in
  both the model docstring and SECURITY.md);
* `cancellation_reason: str | None` (`String(300)`, nullable);
* `created_by_user_id: uuid.UUID` (bare, no FK, required);
* `updated_by_user_id: uuid.UUID | None` (bare, no FK, nullable);
* `created_at`, `updated_at`, `cancelled_at: datetime | None`;
* a concurrency token: since this codebase has no existing optimistic-
  locking precedent, use a plain integer `version` column
  (`nullable=False, default=1`), incremented by the service on every
  update, checked via `WHERE id = :id AND version = :expected_version`
  in the repository's update statement — a 0-row update result means a
  concurrent modification happened, which the service must map to a
  `ConflictError` (see "Concurrency strategy"). Document this as the
  project's first use of optimistic locking and why it was chosen over
  `SELECT ... FOR UPDATE` (simpler to test deterministically, no held
  row lock across a request).

Invariants: `starts_at < ends_at`; `status` transitions restricted (see
"Appointment lifecycle"); tenant-scoped; the half-open interval
convention `[starts_at, ends_at)` applies everywhere overlap is
evaluated (see "Double-booking protection").

---

## Timezone policy

* Every persisted timestamp (`starts_at`, `ends_at`, `created_at`, etc.)
  is `DateTime(timezone=True)` and is always written/read as UTC — no
  exception, matching the rest of the codebase.
* `ProviderSchedule.start_time`/`end_time` and `ScheduleBreak.start_time`/
  `end_time` are plain `time` (no timezone) representing **local
  clinic wall-clock time** — a provider's Monday 09:00–17:00 rule means
  09:00–17:00 in `tenant.timezone`, not UTC. This is why `Tenant` gains a
  `timezone` column in this task.
* The availability engine converts a local wall-clock schedule rule into
  concrete UTC `[starts_at, ends_at)` instants for a given calendar date
  using `zoneinfo.ZoneInfo(tenant.timezone)` — never a naive
  fixed-offset assumption, so DST transitions are handled correctly by
  Python's `zoneinfo` + `datetime` combination, not by this task's own
  arithmetic.
* DST edge cases that must have deterministic, tested behavior:
  * **Spring-forward (nonexistent local time)**: if a schedule's
    `start_time`/`end_time` falls inside the hour that does not exist on
    a given date (e.g. `02:30` on the day clocks jump from `03:00` to
    `04:00`... adjust to the real transition hour for the configured
    timezone), the slot generator must not silently produce an invalid
    or shifted instant — it must either skip that occurrence with a
    documented, tested rule (preferred: shift forward to the next valid
    instant) or raise a controlled error; pick one behavior and test it,
    do not leave it as accidental `zoneinfo`/`datetime` fold behavior.
  * **Fall-back (ambiguous local time)**: an hour that occurs twice must
    resolve deterministically (Python's `datetime(..., fold=0)` = first
    occurrence, `fold=1` = second) — pick `fold=0` (the first/earlier
    occurrence) as the documented convention and test it explicitly.
  * A schedule that spans a DST transition within the same working day
    (rare, but test it) must still produce a correct-duration slot.
* All API responses use ISO 8601 timestamps with an explicit UTC
  offset/`Z` suffix (`datetime.isoformat()` on a tz-aware `datetime`
  already produces this — never strip the offset).
* The frontend never performs naive timezone math: it always renders
  using the tenant's timezone (received from the API, e.g. as part of
  the clinic-context/tenant-context response or a dedicated
  `GET /api/v1/tenant-context` field if that endpoint already exists —
  verify and extend it rather than inventing a parallel one) via the
  browser's `Intl`/`Temporal`-equivalent APIs already available in the
  project's Next.js/React version, not manual offset arithmetic.

## Availability (slot generation) policy

A dynamic **availability service**, not a materialized slot table.
Input: `tenant_id`, `provider_user_id`, `service_type_id`, a date range
(`date_from`, `date_to`), an optional `room_id` filter, and an optional
slot granularity (default: the service type's own duration).

The engine must account for, in order:

1. the provider's active `ProviderSchedule` rows whose
   `[effective_from, effective_until)` covers the requested date and
   whose `day_of_week` matches;
2. subtract that schedule's `ScheduleBreak` rows;
3. subtract any `CalendarBlock` overlapping the provider and/or the
   candidate room for that instant;
4. subtract existing `Appointment` rows in a **blocking status**
   (`SCHEDULED`, `CONFIRMED`) that overlap — `CANCELLED` appointments
   never block; `COMPLETED`/`NO_SHOW` appointments are historical and
   must not block *future* slot generation but must still be considered
   when validating that a *past* interval cannot be double-booked
   retroactively (in practice: past time is excluded from availability
   entirely, so this mostly matters for the DB-level constraint, not the
   slot generator);
5. require the resulting free interval to be at least
   `buffer_before_minutes + default_duration_minutes + buffer_after_minutes`
   long for the requested service type, and report the **bookable**
   slot as the core `[start, start + default_duration_minutes)` window
   (buffers are reserved but not separately exposed as bookable time);
6. exclude any provider whose `TenantMembership` is not `ACTIVE`, any
   inactive room, any inactive service type;
7. exclude any instant in the past relative to "now" (server clock, UTC);
8. enforce a maximum date-range span per request — **31 days** — reject
   a larger range with a `400`, to bound the computation cost of a
   request that would otherwise expand into thousands of candidate
   slots.

Slot generation is a pure, deterministic function of its inputs plus the
current DB state — no database rows are created for unbooked slots. It
must be unit-testable in isolation from the API layer (given schedules/
breaks/blocks/appointments as plain Python inputs, assert the resulting
free intervals), not only via integration tests.

## Appointment lifecycle

Status enum: `SCHEDULED → CONFIRMED → COMPLETED`, with `CANCELLED` and
`NO_SHOW` reachable as documented below. `COMPLETED`, `CANCELLED`, and
`NO_SHOW` are terminal — no further transition is permitted out of them.

Allowed transitions:

| From | To | Trigger |
| --- | --- | --- |
| `SCHEDULED` | `CONFIRMED` | explicit confirm action |
| `SCHEDULED` | `CANCELLED` | cancel action |
| `SCHEDULED` | `NO_SHOW` | no-show action (after `starts_at` has passed) |
| `SCHEDULED` | `COMPLETED` | complete action (after `starts_at` has passed) |
| `CONFIRMED` | `CANCELLED` | cancel action |
| `CONFIRMED` | `NO_SHOW` | no-show action (after `starts_at` has passed) |
| `CONFIRMED` | `COMPLETED` | complete action (after `starts_at` has passed) |

Reschedule (changing `starts_at`/`ends_at`/`provider_user_id`/`room_id`)
is allowed only while `status` is `SCHEDULED` or `CONFIRMED`; rescheduling
does not itself change `status`. Any other transition attempt (e.g.
`CANCELLED → SCHEDULED`, rescheduling a `COMPLETED` appointment) is a
controlled `409` (`invalid_status_transition`), never a silent no-op or
a `500`.

"Complete"/"no-show" before `starts_at` has passed is rejected —
document the exact boundary (allowed once `now >= starts_at`, using
server UTC clock) and test it.

---

## Authorization matrix

Built entirely on the existing `MembershipRole` enum — no new role is
added in this task. Define new role sets in
`backend/app/core/authorization.py` alongside the existing
`READ_ROLES`/`WRITE_ROLES`/etc.:

```python
CALENDAR_READ_ROLES = frozenset({OWNER, MANAGER, OPERATOR, AUDITOR})
CALENDAR_WRITE_ROLES = frozenset({OWNER, MANAGER, OPERATOR})
CALENDAR_CONFIG_ROLES = frozenset({OWNER, MANAGER})
CALENDAR_OVERRIDE_ROLES = frozenset({OWNER, MANAGER})
```

"Provider" is not a role — it is a fact (a `ProviderSchedule`/
`Appointment` row referencing that `user_id`). Every active tenant
member, **regardless of role**, may always view a calendar filtered to
themselves as the provider (`provider_user_id == current user_id`) — a
bare identity check, not a broader grant.

| Operation | Allowed |
| --- | --- |
| View full clinic calendar (all providers) | `CALENDAR_READ_ROLES` |
| View own calendar (self as provider) | any active member (self-scoped only) |
| Manage rooms | `CALENDAR_CONFIG_ROLES` |
| Manage service types | `CALENDAR_CONFIG_ROLES` |
| Manage provider schedules (any provider) | `CALENDAR_CONFIG_ROLES` |
| Create calendar blocks | `CALENDAR_CONFIG_ROLES` |
| Create appointment | `CALENDAR_WRITE_ROLES` |
| Reschedule appointment | `CALENDAR_WRITE_ROLES` |
| Cancel appointment | `CALENDAR_WRITE_ROLES` |
| Mark completed / no-show | `CALENDAR_WRITE_ROLES`, **or** the appointment's own `provider_user_id` acting on their own appointment regardless of role |
| Override availability (book outside schedule/past a block) | `CALENDAR_OVERRIDE_ROLES`, and only with an explicit `override=true` flag plus a required `override_reason` string — never silently |
| View patient contact snapshot (phone/email) | `OWNER`, `MANAGER`, `OPERATOR` — `AUDITOR` sees the appointment (status/time/provider/service) but the API response redacts `patient_phone`/`patient_email` for that role |

`CONTENT_EDITOR` has no calendar permissions in this task — it is
unrelated to scheduling; do not grant it any calendar operation unless a
future task explicitly extends the matrix.

Tenant isolation is non-negotiable: every repository method takes
`tenant_id` explicitly; a resource in another tenant is indistinguishable
from a nonexistent one (`404`, never `403`), matching the existing
cross-tenant convention. No client-supplied `tenant_id`, `provider_id`,
role, or permission claim is ever trusted — everything resolves from
`get_tenant_context`/`TenantMembership` server-side, same as MED-002–004.
Authorization is re-checked in the service layer (authoritative),
independent of any API-layer `require_roles(...)` early rejection.

---

## API contract

New router files under `backend/app/api/`, `/api/v1/...` prefix, following
the existing `require_csrf`/`require_roles`/`get_tenant_context` wiring.
Suggested module names (adapt to whatever is most consistent once the
service layer is written): `rooms.py`, `service_types.py`, `schedules.py`,
`calendar_blocks.py`, `availability.py`, `appointments.py`.

```
GET    /api/v1/rooms
POST   /api/v1/rooms
GET    /api/v1/rooms/{room_id}
PATCH  /api/v1/rooms/{room_id}
POST   /api/v1/rooms/{room_id}/deactivate

GET    /api/v1/service-types
POST   /api/v1/service-types
GET    /api/v1/service-types/{service_type_id}
PATCH  /api/v1/service-types/{service_type_id}
POST   /api/v1/service-types/{service_type_id}/deactivate

GET    /api/v1/provider-schedules
POST   /api/v1/provider-schedules
PATCH  /api/v1/provider-schedules/{schedule_id}
POST   /api/v1/provider-schedules/{schedule_id}/deactivate

GET    /api/v1/calendar-blocks
POST   /api/v1/calendar-blocks
PATCH  /api/v1/calendar-blocks/{block_id}
DELETE /api/v1/calendar-blocks/{block_id}

GET    /api/v1/availability

GET    /api/v1/appointments
POST   /api/v1/appointments
GET    /api/v1/appointments/{appointment_id}
PATCH  /api/v1/appointments/{appointment_id}
POST   /api/v1/appointments/{appointment_id}/reschedule
POST   /api/v1/appointments/{appointment_id}/cancel
POST   /api/v1/appointments/{appointment_id}/confirm
POST   /api/v1/appointments/{appointment_id}/complete
POST   /api/v1/appointments/{appointment_id}/no-show
```

* Explicit action endpoints (`/reschedule`, `/cancel`, `/confirm`,
  `/complete`, `/no-show`) are used for status transitions instead of a
  generic unrestricted `PATCH` — `PATCH /appointments/{id}` is limited to
  non-status metadata (patient contact snapshot, notes, room) and must
  itself reject any attempt to smuggle a `status` field through it.
* `GET /api/v1/availability` query params: `provider_id`, `service_type_id`,
  `room_id` (optional), `date_from`, `date_to` (both required, ISO date,
  max 31-day span — see "Availability policy").
* `GET /api/v1/appointments` supports date-range filtering
  (`date_from`/`date_to`) and pagination (matches whatever pagination
  convention the existing staff-listing endpoint from MED-003 uses —
  verify and reuse it, do not invent a second pagination style).
* Every mutating route: `Depends(get_tenant_context)` +
  `dependencies=[Depends(require_csrf)]`; every route re-validates the
  role inside the service.
* Reads that only need cheap early-rejection use
  `Depends(require_roles(*CALENDAR_READ_ROLES))` directly.

## API response and error contract

Reuse the existing `AppError` subclass hierarchy
(`backend/app/core/errors.py`) and global handler
(`register_error_handlers`) — do not invent a second error-response
shape. Add new `AppError` subclasses as needed, each with a stable
`error_code`-carrying detail (verify how the existing error envelope
exposes a machine-readable code, if at all, and either reuse that
mechanism or extend it consistently — do not silently diverge into a
differently-shaped error body for calendar routes only). Minimum
distinct conditions:

* `400` — invalid interval (`starts_at >= ends_at`), date-range exceeding
  the 31-day cap, malformed date/time input;
* `401` — unauthenticated (existing global behavior);
* `403` — authenticated but insufficient role (existing global
  behavior);
* `404` — resource not found **in the current tenant** (covers
  cross-tenant access — never distinguishable from "doesn't exist");
* `409` — machine-readable conflict codes:
  * `appointment_conflict` — provider or room overlap;
  * `provider_unavailable` — outside any active schedule, or inside a
    break;
  * `room_unavailable` — room inactive or blocked;
  * `outside_schedule` — no matching `ProviderSchedule` for that instant;
  * `blocked_period` — overlaps a `CalendarBlock`;
  * `invalid_status_transition`;
  * `stale_version` — optimistic-lock mismatch on update/reschedule;
* `422` — Pydantic validation error (existing FastAPI default contract —
  do not override it);
* `429` — only if an existing rate-limiter is reused for the
  availability endpoint (see "Security requirements" — evaluate, do not
  assume);
* `5xx` — controlled, generic, no internal detail leaked (existing
  `AppError`/unhandled-exception behavior).

---

## Concurrency strategy

* **Database-level double-booking protection is mandatory** and is the
  final authority — a service-layer pre-check alone is not sufficient
  and must never be presented as sufficient.
* Appointment intervals are **half-open**: `[starts_at, ends_at)` — an
  appointment ending at `10:30` and another starting at `10:30` for the
  same provider/room do not overlap and must both be allowed.
* Add, in the MED-005 migration:
  ```sql
  CREATE EXTENSION IF NOT EXISTS btree_gist;
  ```
  documented as a new prerequisite (verify the disposable
  `postgres-test` service and the dev `postgres` service can both create
  it — `postgres:17-alpine` supports it out of the box; if the migration
  cannot create the extension in some environment, it must fail loudly,
  never silently skip the constraint).
* Add a PostgreSQL exclusion constraint on `appointments` using
  SQLAlchemy's `sqlalchemy.dialects.postgresql.ExcludeConstraint` (or the
  Alembic-equivalent raw DDL if the SQLAlchemy version in
  `backend/pyproject.toml` does not support declaring it via the ORM
  model directly — verify at implementation time and document which
  approach was used and why):
  * one exclusion constraint scoped to `(tenant_id, provider_user_id)`
    equality plus `tstzrange(starts_at, ends_at, '[)')` overlap, with a
    partial `WHERE status IN ('scheduled', 'confirmed')` condition (only
    blocking statuses participate);
  * a second, separate partial exclusion constraint scoped to
    `(tenant_id, room_id)` equality plus the same range/overlap logic,
    additionally gated `WHERE room_id IS NOT NULL AND status IN ('scheduled', 'confirmed')`
    (a `NULL` room never participates in an equality-based exclusion
    constraint by Postgres's own null-handling semantics for `=` — confirm
    this is sufficient rather than assuming it).
* A constraint violation on insert/update must be caught in the
  repository/service (via `IntegrityError.orig.diag.constraint_name`,
  matching the existing `staff_service.py` pattern) and mapped to the
  `409 appointment_conflict` domain error — never a raw `500` or an
  unhandled `IntegrityError`.
* `Appointment.version` optimistic locking (see Domain model) covers
  non-overlap conflicting updates (e.g. two concurrent reschedules of the
  *same* appointment) — the DB exclusion constraint covers overlap
  between *different* appointments. Both are required; neither alone is
  sufficient.
* `CalendarBlock`-vs-`Appointment` conflict prevention is enforced at the
  service layer (availability check before insert), **not** by a
  database constraint spanning both tables — document this explicitly as
  a narrower guarantee than the appointment-vs-appointment case, and
  note the (small, and product-acceptable for a foundation slice) residual
  race window between the read and the write, with a possible future
  enhancement (e.g. a trigger or a covering constraint) left as an
  explicit TODO in the code, not silently unaddressed in the docs.
* Required tests (real disposable PostgreSQL, not SQLite):
  * two concurrent `POST /appointments` for the *same* provider and
    overlapping intervals — exactly one succeeds, the other receives
    `409 appointment_conflict`;
  * the same for two concurrent creates against the *same room*;
  * concurrent reschedule of two different appointments into the same
    slot — same outcome;
  * a reschedule racing a cancel of the same appointment — the
    `version` check must produce a controlled `409`, not a corrupted
    row;
  * adjacent (touching, not overlapping) intervals for the same
    provider/room are both accepted (half-open boundary correctness).

---

## Audit requirements

Use the existing `emit_audit_event`/`AuditEvent`/`AuditOutcome` — no new
audit infrastructure. Minimum event types (`event_type` string):

```
calendar.room_created
calendar.room_updated
calendar.room_deactivated
calendar.service_type_created
calendar.service_type_updated
calendar.service_type_deactivated
calendar.schedule_created
calendar.schedule_updated
calendar.schedule_deactivated
calendar.block_created
calendar.block_updated
calendar.block_removed
appointment.created
appointment.rescheduled
appointment.cancelled
appointment.confirmed
appointment.completed
appointment.no_show
appointment.override_used
```

Rules (matching the existing MED-002–004 convention exactly):

* success audits are emitted only **after** commit;
* rejected attempts (insufficient role, conflict, invalid transition)
  are audited with `AuditOutcome.REJECTED` **before/without** any
  mutation;
* an audit event never contains: the raw session/CSRF token, a password,
  `patient_phone`/`patient_email` in full (if included at all, follow
  whatever minimization/redaction convention SECURITY.md documents for
  PII in logs — when in doubt, omit rather than include), free-text
  `notes`, or any other field broader than what's needed to identify
  *which* resource changed and *what kind* of change happened
  (`target_resource_type`, `target_resource_id`, `tenant_id`,
  `actor_user_id`, `outcome`).
* `appointment.conflict_rejected` may be added only if it can be emitted
  without ever implying a mutation occurred — evaluate at implementation
  time whether this adds real audit value versus noise; it is optional,
  unlike the events listed above.

---

## Security requirements

* Strict tenant isolation everywhere (see Authorization matrix) —
  cross-tenant access is `404`, never `403`, never a different response
  shape.
* CSRF (`require_csrf`) on every mutating calendar/appointment route.
* No client-supplied `tenant_id`/`provider role`/permission is ever
  trusted — always resolved server-side from `get_tenant_context` +
  `TenantMembership`.
* `GET /api/v1/availability` and `GET /api/v1/appointments` (date-range
  queries) are the highest-frequency read endpoints this task adds —
  evaluate whether the existing Redis-backed rate limiter
  (`app.core.rate_limit`) should be applied; if it is not applied,
  document why the 31-day cap and normal auth/DB load are considered
  sufficient instead of silently doing nothing.
* Enforce the 31-day availability date-range cap and pagination on
  `GET /appointments` — both are security-relevant (unbounded query
  cost), not just UX conveniences.
* Patient contact snapshot handling: never logged, never included in
  audit payloads beyond what "Audit requirements" allows, redacted from
  `AUDITOR`-role API responses (see Authorization matrix), never
  returned to a caller outside the owning tenant.
* No raw SQL/DB exception text ever reaches an API response — every
  `IntegrityError` from the new exclusion/check constraints is caught
  and mapped to a controlled domain error (see Concurrency strategy).
* Transaction atomicity for every mutation (create/reschedule/cancel/
  status-change) — no partial state on failure, matching the existing
  commit-then-audit pattern.
* Inactive provider (`TenantMembership.status != ACTIVE`), inactive
  room, inactive service type, and stale/invalid session all fail
  closed, consistent with MED-002–004's existing behavior — MED-005 adds
  no new session/auth mechanism of its own.

---

## Frontend scope

Canonical route: **`/calendar`** (not `/appointments` — reserve
`/appointments` conceptually for a possible future list-only view; this
task builds the calendar-first experience). Document this choice in the
task and do not introduce a second, competing route for the same
feature.

### Calendar page (`/calendar`)

* day view and week view, with date navigation and a "Today" shortcut;
* provider filter, room filter, service-type filter;
* appointment cards showing time, patient display name, service,
  provider, room, status;
* visual representation of blocked periods (`CalendarBlock`) distinct
  from booked appointments;
* loading, empty, and error states (no silent blank screens);
* responsive layout (reuse whatever breakpoint conventions the existing
  settings pages already use, if any — otherwise keep it simple and
  documented);
* renders all times in the tenant's timezone (never the browser's local
  timezone if it differs — fetch `tenant.timezone` and use it
  explicitly).

### Appointment creation

Provider → service type → optional room → date → available slot
(fetched from `GET /availability`) → patient display name (required) →
optional phone/email → optional notes → confirm. On a `409` conflict,
refresh availability and show a clear, specific message (never a raw
error string) — never silently retry with the same interval.

### Appointment details

Status, provider, room, service, start/end (tenant timezone), patient
snapshot, created/updated metadata, and role-appropriate actions
(reschedule/cancel/confirm/complete/no-show), each hidden or disabled
per the Authorization matrix (client-side hiding is UX only — the
backend re-enforces everything, matching the existing MED-003/004
pattern of "local re-derivation purely for UI, never trusted").

### Reschedule

Load the current appointment, fetch new availability, confirm, handle
`409 appointment_conflict`/`stale_version` by refreshing state and
re-prompting — never silently overwrite.

### Cancel

Confirmation dialog, required reason, success state, calendar refresh.

### Administration settings

Add new sections alongside the existing `/settings/clinic`,
`/settings/staff`, `/settings/security` pages (same
`frontend/app/settings/layout.tsx` shell/nav): rooms, service types,
provider schedules, calendar blocks. No patient-registry UI, no
notifications UI, no billing UI (Non-goals).

## Frontend technical requirements

* Use the existing authenticated layout and tenant/session context — no
  new auth mechanism.
* All API calls go through `apiFetch` (`frontend/app/lib/api.ts`) — CSRF
  header attached automatically, `credentials: "include"`, no token in
  `localStorage`.
* Accessible forms (labeled inputs, keyboard-navigable date/slot
  pickers).
* No naive date parsing (`new Date("2024-01-01")` string-splicing) —
  parse ISO 8601 with offset explicitly.
* No optimistic "success" UI before the API call actually commits —
  wait for the response, then update state.
* Conflict responses trigger an availability refresh, not a blind retry.
* No cross-tenant resource leakage in any client-side state.
* `npm run lint`, typecheck, and production build must all pass.
* Evaluate whether a maintained calendar/grid component is warranted or
  whether a native custom grid is sufficient for this foundation slice;
  if a new frontend dependency is added, justify it in the PR/commit
  description (a lightweight ADR-style paragraph is sufficient — this
  project does not have a formal ADR log) rather than adding it silently.

---

## Database and migration requirements

One new Alembic revision, chained after `65f7891a7fc7`
(`down_revision = "65f7891a7fc7"`) — never edit a released migration.
Generate the actual revision id via the normal `alembic revision`
workflow at implementation time (do not hand-invent a hash now). The
migration must include:

* `CREATE EXTENSION IF NOT EXISTS btree_gist;`
* `ALTER TABLE tenants ADD COLUMN timezone ...` with a `server_default`;
* six new tables: `clinic_rooms`, `appointment_service_types`,
  `provider_schedules`, `schedule_breaks`, `calendar_blocks`,
  `appointments`;
* every enum column as `sa.Enum(..., native_enum=False, ...)`, lowercase
  values, matching the model layer exactly;
* explicit, stable constraint/index names for every FK, unique
  constraint, check constraint, and the two exclusion constraints
  (`ex_appointments_provider_overlap`, `ex_appointments_room_overlap`, or
  equivalent explicit names — never Postgres's auto-generated default);
* tenant-scoped indexes (`(tenant_id, ...)`) for every table's common
  query pattern (date-range lookups especially — e.g.
  `(tenant_id, provider_user_id, starts_at)`,
  `(tenant_id, room_id, starts_at)`);
* a full, tested `downgrade()` (including dropping the exclusion
  constraints and the new tenant column — but the migration does **not**
  need to `DROP EXTENSION btree_gist` on downgrade if other
  future-tenant data could depend on it; document that decision either
  way).

Migration validation (matching MED-002–004's precedent): upgrade from
current `master` head to the MED-005 head, downgrade of the MED-005
revision, re-upgrade to head, schema/constraint verification (including
confirming the exclusion constraints actually exist and actually reject
an overlapping row when tested directly against the disposable test
database) — all against the disposable `postgres-test` service, never
the normal development database.

## Repository and service architecture

Repositories (one per entity, matching the existing
`<Entity>Repository(db: Session)` convention, `tenant_id`-scoped
methods, `flush()` only, no `commit()`):

* `ClinicRoomRepository`, `AppointmentServiceTypeRepository`,
  `ProviderScheduleRepository`, `ScheduleBreakRepository` (or embedded
  inside the schedule repository if that proves cleaner — justify the
  choice), `CalendarBlockRepository`, `AppointmentRepository`.

Services (own the transaction, re-check authorization, commit before
audit):

* `RoomService`, `ServiceTypeService`, `ScheduleService`,
  `AvailabilityService` (pure computation, no DB writes — see
  "Availability policy"), `AppointmentService`.

No business logic in API routes. `AvailabilityService` must be directly
unit-testable with plain Python inputs (schedules/breaks/blocks/
appointments as in-memory objects), independent of the database, in
addition to the integration tests that exercise it through the real API
and a real database.

## Pydantic schemas

Separate schemas for create / update / response / list-filter / action
requests (reschedule/cancel/confirm/complete/no-show) / availability
result / conflict error metadata — never expose internal-only fields
(e.g. `version` may be exposed read-only for optimistic-lock UX, but
`created_by_user_id`'s exposure should be deliberate, not accidental).
`patient_phone`/`patient_email` are omitted or redacted in the response
schema variant served to `AUDITOR`-role callers (see Authorization
matrix) — implement this as a distinct response model or a
serialization-time filter, not an ad-hoc per-field `if role == ...`
scattered across the route.

---

## Testing requirements

No fixed target test count is specified — write what the scope above
actually requires, and ensure every existing test (currently 431 backend
tests: 119 unit + 312 integration, plus frontend lint/typecheck/build)
continues to pass unmodified.

### Unit tests (minimum)

Time-interval operations; slot generation (schedules − breaks − blocks −
existing appointments, in isolation from the DB); duration/buffer
arithmetic; break subtraction; block subtraction; timezone conversion
(local wall-clock ↔ UTC); the documented DST spring-forward and
fall-back behaviors; every status-transition rule (valid and invalid);
the authorization-matrix policy functions; conflict-code mapping;
patient-snapshot normalization (phone/email).

### Integration tests (minimum, real disposable PostgreSQL)

Room CRUD + tenant isolation; service-type CRUD; provider-schedule CRUD
+ overlapping-rule rejection; breaks; calendar blocks; availability
(happy path and every subtraction case above, through the real API);
appointment creation (happy path); provider conflict (DB-level,
concurrent); room conflict (DB-level, concurrent); adjacent
(non-overlapping, touching) intervals allowed; a cancelled appointment
frees its slot; reschedule (happy path, conflict, stale-version);
cancel; confirm; complete; no-show; every invalid transition; inactive
provider/membership/room/service-type rejected; cross-tenant access
(`404`); CSRF missing/invalid/valid; unauthenticated access; every role
in the authorization matrix (positive and negative); every audit event
(emitted on success after commit, emitted as `REJECTED` on rejection,
never on a rolled-back mutation); the migration's exclusion constraints
directly (insert a conflicting row via raw SQL/the ORM and assert the DB
itself rejects it, independent of the service layer).

### Frontend checks (minimum)

Filter/query-policy helpers; date/time formatting + timezone-display
helpers; appointment-form validation; conflict-handling flow;
permission-based action visibility; `npm run lint`; typecheck;
production build.

---

## Documentation requirements

Update, minimally and only where the current text would otherwise be
inaccurate once this task lands:

* `ARCHITECTURE.md` — mark `appointments`/`schedules` as
  implemented (foundation) in the "Planned modules" table, add a section
  describing the availability engine, the double-booking DB strategy,
  and the tenant-timezone model, consistent in tone/depth with the
  existing "Authentication and user identity" section;
* `SECURITY.md` — document the patient-contact-snapshot minimization
  policy, the audit-payload boundaries for calendar events, and the
  tenant-isolation guarantees for appointment data;
* `README.md` — only if local-dev setup changes (e.g. a note that the
  test database needs `btree_gist`, if that isn't automatic);
* this task file, kept accurate if scope is clarified during
  implementation (do not let the spec silently drift out of sync with
  the code).

Never claim notifications, billing, or a full patient registry exist.

## Quality gates and Codex review

Run `.ai-workflow/scripts/run-tests.ps1` and require every configured
check to pass (ruff, mypy, backend unit + integration tests, frontend
lint/typecheck/build, Docker config checks, backend image build, git
diff check). Run migration validation against the disposable test
database (see "Database and migration requirements"). Run
`.ai-workflow/scripts/run-review.ps1` in **incremental mode**
(`-Incremental -BaselineCommit <last-completed-review's-target-commit>`)
— the tooling built in the MED-004 repair rounds supports this
specifically so a long implementation doesn't have to re-embed the
entire branch diff on every repair round; use it rather than falling
back to `-FullBranch` by default. Stop for human approval on the
resulting verdict — do not automatically repair Codex findings.

## Migration evidence requirements

Same evidence shape as MED-002–004: captured upgrade/downgrade/
re-upgrade output, schema/constraint verification output (including the
exclusion constraints), confirmation the normal development database was
never touched, saved as `reports/migration-validation-latest.md` (never
committed — `reports/` stays out of every commit, per this repository's
established convention).

## Codex review requirements

A genuinely completed Codex verdict (not `REVIEW_INCOMPLETE`, not a
process failure) is required before this task can be considered
implementation-complete. `APPROVED` with zero findings is the target;
`CHANGES_REQUIRED` findings must be reported precisely and either fixed
(with a fresh review) or explicitly deferred with the human's sign-off —
never auto-approved by the implementing agent itself.

---

## Acceptance criteria

* All six new models exist with the migration applied, verified, and
  reversible;
* `Tenant.timezone` exists, validated, defaulted;
* every invariant listed under "Domain model" is enforced (DB constraint
  where specified, service-layer validation otherwise, documented which
  is which);
* the availability engine produces correct slots against every
  subtraction case (schedule, breaks, blocks, existing appointments) and
  handles the documented DST cases deterministically;
* the appointment lifecycle enforces exactly the transitions listed
  above, no others;
* the authorization matrix is enforced server-side for every operation,
  with tenant isolation returning `404` for cross-tenant resources;
* the PostgreSQL exclusion constraints exist, are named explicitly, and
  are proven (by a real, direct test) to reject a conflicting insert;
* every audit event listed is emitted with correct timing (success only
  after commit) and safe payloads;
* every API endpoint listed exists, tenant-scoped, CSRF-protected where
  mutating, with the documented error contract;
* the `/calendar` frontend page and the four new settings sections work
  end-to-end against a real backend;
* all existing tests still pass; new tests cover every item in "Testing
  requirements";
* documentation updated as scoped above;
* all quality gates pass;
* migration evidence captured;
* Codex review completed with a genuine verdict.

## Definition of Done

MED-005 is complete only when: every model/migration/constraint above
exists and is verified; tenant isolation is verified; the authorization
matrix is implemented and tested; the availability engine is implemented
and unit-tested independent of the API; the appointment lifecycle is
implemented with enforced transitions; double-booking is protected at
the database level and proven by a concurrency test; the frontend
calendar foundation and admin settings pages are implemented; audit
events are implemented; documentation is updated; all quality gates
pass; migration evidence is captured; Codex review is completed with a
genuine (not incomplete) verdict; no generated report is committed;
`.env` is unchanged; the normal development database is unchanged; and
nothing is pushed, merged, or deployed unless a separate, explicit task
instructs otherwise.

## Implementation sequence

1. architecture inspection (re-verify this task's "Current architecture
   assumptions" section against the actual code at implementation time —
   this spec is a snapshot, not a guarantee);
2. domain enums and models (`Tenant.timezone` + the six new models);
3. Alembic migration (including `btree_gist` + exclusion constraints),
   upgrade/downgrade/re-upgrade validated;
4. repositories;
5. interval/timezone utility module (pure functions, unit-tested first);
6. `ScheduleService`;
7. `AvailabilityService`;
8. `AppointmentService` (create/reschedule/cancel/confirm/complete/
   no-show, optimistic locking, conflict mapping);
9. authorization policy sets in `app.core.authorization`;
10. Pydantic schemas + API routes;
11. audit integration;
12. backend unit + integration tests;
13. frontend `/calendar` page;
14. frontend settings sections (rooms/service-types/schedules/blocks);
15. frontend checks (lint/typecheck/build);
16. documentation updates;
17. migration validation capture;
18. full quality gates;
19. Codex review (incremental mode);
20. local commits (small, one logical change per commit — unlike this
    initialization task, which is a single commit);
21. stop before push — pushing/opening a PR requires a separate,
    explicit instruction, matching the MED-004 precedent.
