# MED-002 — Multi-Tenant Foundation

## Objective

Implement the minimum secure multi-tenant foundation for the clinic administration platform.

The platform will serve multiple independent healthcare organizations. Every tenant-owned resource must be isolated server-side so that users from one tenant cannot access, modify, enumerate, or infer data belonging to another tenant.

This task establishes the tenant domain, tenant membership, request-level tenant context, authorization checks, repository scoping, database migration, and regression tests.

Do not implement authentication UI, invitations, billing, subscriptions, clinics, practitioners, appointments, or other business modules in this task.

---

## Governing documents

Before changing code, read:

* `AGENTS.md`
* `CLAUDE.md`
* `ARCHITECTURE.md`
* `SECURITY.md`
* `CONTRIBUTING.md`
* `.ai-workflow/config.json`

Inspect the current backend architecture, database setup, Alembic configuration, dependency injection, API routing, error-handling conventions, and test structure.

Reuse existing conventions wherever they are sound.

Do not redesign unrelated parts of the project.

---

## Required domain model

### Tenant

Create a persistent `Tenant` entity representing an independent organization using the platform.

Minimum fields:

* `id`
* `name`
* `slug`
* `status`
* `created_at`
* `updated_at`

Requirements:

* use the repository's existing ID convention;
* `slug` must be unique;
* normalize and validate `slug`;
* define explicit active and inactive states;
* timestamps must follow the project's existing timezone convention;
* avoid soft-delete behavior unless the project already has an established pattern;
* do not add clinic-specific fields yet.

Recommended tenant statuses:

* `active`
* `inactive`

Use an enum or constrained representation consistent with the current codebase.

### TenantMembership

Create a persistent `TenantMembership` entity connecting a user identity to a tenant.

Minimum fields:

* `id`
* `tenant_id`
* `user_id`
* `role`
* `status`
* `created_at`
* `updated_at`

Requirements:

* one membership per `tenant_id` and `user_id`;
* foreign key to `Tenant`;
* use the current or planned user identifier representation without implementing the full authentication system;
* do not create a duplicate user model if one already exists;
* if no user model exists, use a clearly documented placeholder identifier compatible with future authentication;
* enforce uniqueness at database level;
* define explicit active and inactive membership states.

Required roles:

* `owner`
* `manager`
* `operator`
* `content_editor`
* `auditor`

Do not implement a complete RBAC framework yet. Only establish the membership role representation and authorization primitives needed for tenant access.

---

## Tenant context

Implement an explicit request-level tenant context.

The tenant context must contain at least:

* authenticated user identifier;
* selected tenant identifier;
* validated active membership;
* membership role.

The context must be established server-side through a dependency or equivalent framework mechanism.

Requirements:

1. The tenant identifier may be supplied through a documented request header or route context for this foundation task.
2. The tenant identifier must never be trusted by itself.
3. The server must validate that:

   * the tenant exists;
   * the tenant is active;
   * the user has a membership in that tenant;
   * the membership is active.
4. The resolved context must use database-backed tenant and membership data.
5. Request body data must not be allowed to override the validated tenant context.
6. Missing tenant context must be rejected for tenant-scoped routes.
7. Invalid tenant context must be rejected consistently.
8. The dependency must be reusable by future modules.
9. Do not rely on frontend filtering.
10. Do not use a global mutable tenant variable.

If authentication is not implemented yet, use a clearly isolated development-only identity provider or test dependency.

The temporary identity mechanism must:

* be explicitly marked as non-production;
* be replaceable by the future authentication layer;
* not accept arbitrary identity silently in production configuration;
* be covered by tests;
* not weaken the future authorization model.

---

## Tenant-scoped repository pattern

Create a reusable server-side pattern for tenant-owned database operations.

The design must ensure that future repositories cannot accidentally retrieve resources without tenant filtering.

At minimum, demonstrate the pattern with a small non-business test resource or internal tenant-scoped example entity.

Do not implement clinic, practitioner, patient, conversation, appointment, or medical entities for demonstration purposes.

The demonstration entity may be named something clearly internal, such as:

* `TenantResource`
* `TenantScopedRecord`
* `TenantNote`

It must not contain patient or healthcare data.

Minimum fields:

* `id`
* `tenant_id`
* a harmless test value such as `name`
* `created_at`
* `updated_at`

Required repository behavior:

* create within the validated tenant context;
* get by ID within the validated tenant;
* list within the validated tenant;
* update within the validated tenant;
* delete within the validated tenant;
* never expose an unscoped repository method for tenant-owned records;
* tenant ID must be a required server-side argument or come from the validated context;
* cross-tenant access must not reveal whether the foreign resource exists.

Prefer returning `404` for cross-tenant object access if that matches the project's security convention, because it reduces resource enumeration risk.

Document the chosen convention.

---

## API foundation

Add minimal development or internal API routes demonstrating tenant-scoped access.

Suggested route group:

```text
/api/v1/tenant-context
/api/v1/tenant-resources
```

Required behavior:

### Tenant context route

A read-only endpoint may return non-sensitive context information:

* tenant ID;
* tenant name;
* membership role;
* membership status.

Do not expose internal security details, database metadata, or unrelated user data.

### Tenant resource routes

Create minimal CRUD routes for the harmless tenant-scoped demonstration entity.

The routes exist only to validate the architecture and tests.

Requirements:

* all routes require validated tenant context;
* create operations derive `tenant_id` from context;
* clients cannot choose or override `tenant_id` in request bodies;
* reads, updates, and deletes are tenant-scoped;
* request and response schemas must not expose unnecessary fields;
* use existing response and error conventions;
* validate input lengths and formats.

---

## Authorization primitives

Implement minimal reusable authorization helpers.

Required helpers:

* require active tenant membership;
* require one of a set of tenant roles;
* expose the current membership role to services;
* reject insufficient roles consistently.

For the demonstration API:

* all active roles may read;
* `owner`, `manager`, `operator`, and `content_editor` may create and update;
* only `owner` and `manager` may delete;
* `auditor` is read-only.

Do not build a complete permission database or policy engine yet.

The implementation must be easily replaceable or extensible by future RBAC and policy modules.

---

## Service layer

Business actions must pass through deterministic services.

Create a tenant service responsible for:

* retrieving tenants;
* validating tenant state;
* retrieving memberships;
* validating membership state;
* resolving tenant context.

Create a service for the demonstration tenant resource.

Requirements:

* routes must not contain direct database business logic;
* services must receive validated tenant context;
* repositories must enforce tenant scope;
* role checks must occur before state changes;
* avoid duplicated tenant-validation logic;
* do not let schemas or request input decide tenant ownership.

---

## Background-job tenant context

Create a reusable serializable tenant execution context for future background jobs.

It must contain only the minimum identifiers required to revalidate authorization or ownership when a job runs.

Minimum fields:

* `tenant_id`
* initiating `user_id`
* optional correlation or request ID

Requirements:

* background-job context must be explicit;
* absence of tenant context must fail closed for tenant-owned operations;
* do not serialize database objects or membership objects;
* document that workers must revalidate relevant tenant state before executing sensitive actions;
* add tests for missing or invalid background-job tenant context.

Do not implement a full job queue in this task.

---

## Audit events

Add a minimal audit abstraction for tenant-sensitive changes.

At minimum, record or emit structured audit events for:

* tenant resource creation;
* tenant resource update;
* tenant resource deletion;
* rejected cross-tenant mutation attempts where safely detectable;
* rejected insufficient-role mutations.

The audit event must include:

* event type;
* tenant ID;
* actor user ID;
* target resource type;
* target resource ID when available;
* timestamp;
* correlation or request ID when available;
* outcome.

Do not include:

* secrets;
* request bodies;
* sensitive healthcare data;
* authentication tokens.

If the project does not yet have a persistent audit store, implement a clean audit interface and a safe structured logging adapter suitable for later replacement.

Do not introduce a large audit subsystem in this task.

---

## Database migration

Create an Alembic migration for all new persistent entities.

The migration must:

* create tenant-related enums or constraints safely;
* create `tenants`;
* create `tenant_memberships`;
* create the harmless tenant-scoped demonstration table;
* create foreign keys;
* create unique constraints;
* create required indexes;
* support downgrade;
* follow existing naming conventions;
* not modify unrelated tables.

Required constraints and indexes include:

* unique tenant slug;
* unique tenant membership per tenant and user;
* index on membership user ID;
* index on membership tenant ID;
* index on tenant-scoped resource tenant ID;
* composite index where useful for tenant and resource lookup.

Validate migration upgrade and downgrade against a disposable development database.

---

## Configuration

Add only configuration required by this task.

If a development identity provider is introduced, add explicit environment settings such as:

```text
DEVELOPMENT_IDENTITY_ENABLED=false
```

Default behavior must be safe.

Requirements:

* production-like default must not trust arbitrary user headers;
* development-only behavior must require explicit enabling;
* add safe examples to `.env.example`;
* do not create or modify `.env`;
* do not add real credentials;
* document configuration in `README.md` only where necessary.

---

## Security requirements

The following are mandatory:

1. Tenant isolation must be enforced in backend queries.
2. A user-supplied tenant ID is not authorization.
3. Membership must be validated from the database.
4. Inactive tenants must be rejected.
5. Inactive memberships must be rejected.
6. Cross-tenant reads must not disclose foreign resource existence.
7. Cross-tenant updates and deletes must be rejected.
8. Tenant IDs in request bodies must be ignored or rejected.
9. Tenant-scoped services must require explicit validated context.
10. Background operations must fail closed without tenant context.
11. Logs must not contain secrets or sensitive payloads.
12. No global mutable current-tenant state.
13. No repository method may return all tenant-owned records without scope.
14. Tests must use at least two tenants.
15. Tests must use overlapping resource identifiers or equivalent scenarios where practical.
16. Do not rely solely on UUID unpredictability for security.
17. Do not introduce insecure development behavior into default production configuration.
18. Avoid timing or error differences that unnecessarily reveal foreign resource existence.
19. Do not implement medical or patient data.
20. Do not weaken existing health, readiness, Docker, lint, type, or test checks.

---

## Required test fixtures

Create reusable test fixtures for:

* Tenant A;
* Tenant B;
* active user in Tenant A;
* active user in Tenant B;
* user with memberships in both tenants;
* inactive tenant;
* inactive membership;
* owner role;
* manager role;
* operator role;
* content editor role;
* auditor role;
* tenant resource in Tenant A;
* tenant resource in Tenant B.

Do not use real names or patient information.

---

## Mandatory unit tests

Add unit tests for:

### Tenant service

* active tenant resolves successfully;
* inactive tenant is rejected;
* missing tenant is rejected;
* active membership resolves successfully;
* missing membership is rejected;
* inactive membership is rejected;
* role is exposed correctly.

### Authorization helpers

* allowed role succeeds;
* disallowed role is rejected;
* auditor cannot mutate;
* owner can delete;
* operator cannot delete;
* missing context is rejected.

### Tenant resource service

* create derives tenant ID from context;
* request payload cannot override tenant ID;
* tenant-scoped get works;
* foreign-tenant get returns the selected secure response;
* tenant-scoped update works;
* foreign-tenant update is rejected;
* tenant-scoped delete works;
* foreign-tenant delete is rejected.

### Background-job context

* valid context can be serialized;
* missing tenant ID is rejected;
* missing actor ID is rejected where required;
* invalid tenant state is revalidated before execution;
* tenant-owned operation without context fails closed.

---

## Mandatory integration and API tests

Add integration tests covering:

1. active member accesses own tenant context;
2. user without membership is rejected;
3. inactive membership is rejected;
4. inactive tenant is rejected;
5. unknown tenant is rejected;
6. missing tenant header or route context is rejected;
7. missing development identity is rejected;
8. Tenant A lists only Tenant A resources;
9. Tenant B lists only Tenant B resources;
10. Tenant A cannot read Tenant B resource;
11. Tenant A cannot update Tenant B resource;
12. Tenant A cannot delete Tenant B resource;
13. Tenant A cannot create a resource owned by Tenant B;
14. request payload containing another `tenant_id` is rejected or safely ignored;
15. auditor may read;
16. auditor cannot create;
17. auditor cannot update;
18. auditor cannot delete;
19. operator may create and update;
20. operator cannot delete;
21. manager can delete;
22. owner can delete;
23. API responses do not expose membership internals unnecessarily;
24. error responses do not reveal foreign resource existence;
25. audit event is emitted for successful create;
26. audit event is emitted for successful update;
27. audit event is emitted for successful delete;
28. insufficient-role mutation emits a safe rejection audit event;
29. existing `/health` still works;
30. existing `/ready` still works.

Use the real database integration method already established by the project where practical.

Do not replace all integration behavior with mocks.

---

## Migration validation

Run and document:

1. migration from an empty database to head;
2. downgrade of the new revision;
3. upgrade to head again;
4. backend startup after migration;
5. test suite after migration.

Use a disposable local development or test database.

Do not run migrations against any external or production database.

---

## Quality gates

Run all checks configured by the repository.

At minimum, where supported:

```text
Ruff check
Ruff formatting check
mypy
pytest
Alembic migration validation
Docker Compose config validation
backend image build
frontend lint
frontend typecheck
frontend production build
git diff --check
```

Do not skip a failing check.

Do not modify tests merely to hide failures.

If a pre-existing unrelated check fails, document it clearly and prove whether the failure existed before this task.

---

## Documentation updates

Update documentation only where necessary.

Required documentation:

### `ARCHITECTURE.md`

Add:

* tenant domain;
* tenant context resolution;
* membership validation;
* repository scoping;
* authorization flow;
* background-job tenant context;
* audit-event flow;
* secure cross-tenant behavior.

Clearly mark implemented versus planned functionality.

### `SECURITY.md`

Add:

* tenant-boundary threat model;
* IDOR prevention;
* tenant enumeration prevention;
* development identity restrictions;
* fail-closed background operations;
* mandatory cross-tenant regression testing.

### `README.md`

Add only the minimum local instructions required to:

* enable the development identity provider;
* identify the tenant and development user for local API testing;
* run migrations;
* run tenant-isolation tests.

Do not expose insecure production instructions.

---

## Out of scope

Do not implement:

* production authentication;
* login UI;
* password handling;
* OAuth;
* OpenID Connect;
* session management;
* invitation flows;
* user management UI;
* billing;
* subscriptions;
* clinic locations;
* practitioners;
* medical services;
* schedules;
* appointments;
* patient records;
* conversations;
* knowledge base;
* notifications;
* full audit database;
* policy engine;
* Qdrant tenant collections;
* Redis tenant caching;
* frontend tenant UI;
* production deployment;
* Kubernetes;
* external APIs.

---

## Acceptance criteria

### AC-01

A valid active member can resolve tenant context for an active tenant.

### AC-02

A user without membership cannot resolve tenant context.

### AC-03

An inactive membership cannot resolve tenant context.

### AC-04

An inactive tenant cannot be used.

### AC-05

A missing or unknown tenant is rejected.

### AC-06

Tenant A cannot list Tenant B resources.

### AC-07

Tenant A cannot read Tenant B resources.

### AC-08

Tenant A cannot update Tenant B resources.

### AC-09

Tenant A cannot delete Tenant B resources.

### AC-10

Clients cannot assign resource ownership through request payloads.

### AC-11

Tenant filtering is enforced server-side in repositories or equivalent data-access boundaries.

### AC-12

No public unscoped tenant-resource repository operation exists.

### AC-13

Role-based mutation restrictions work as specified.

### AC-14

Auditors are read-only.

### AC-15

Background tenant operations fail closed without explicit context.

### AC-16

Tenant-sensitive mutations emit safe audit events.

### AC-17

Database constraints enforce tenant slug uniqueness and membership uniqueness.

### AC-18

Alembic upgrade succeeds on an empty database.

### AC-19

Alembic downgrade of the new revision succeeds.

### AC-20

Re-upgrade succeeds after downgrade.

### AC-21

All new unit tests pass.

### AC-22

All new integration tests pass.

### AC-23

All existing tests continue to pass.

### AC-24

Ruff passes.

### AC-25

mypy passes.

### AC-26

Docker Compose configuration remains valid.

### AC-27

Backend Docker image builds successfully.

### AC-28

Frontend lint, typecheck, and production build remain successful.

### AC-29

Existing `/health` and `/ready` endpoints remain functional.

### AC-30

No secrets, credentials, patient data, or sensitive payloads are added to the repository or logs.

### AC-31

No unrelated application modules are introduced.

### AC-32

No commit, push, merge, or deployment is performed automatically.

---

## Implementation process

1. Inspect the repository.
2. Print a concise implementation plan.
3. Identify affected files.
4. Create a task branch if required by the existing workflow.
5. Implement the smallest coherent solution.
6. Add migrations.
7. Add unit tests.
8. Add integration tests.
9. Run migration validation.
10. Run all configured quality gates.
11. Generate the implementation report.
12. Stop for Codex and human review.

Do not stop after planning unless a genuine technical blocker prevents safe implementation.

Do not ask for confirmation for ordinary repository-local implementation steps.

Stop and report clearly if:

* the existing architecture conflicts materially with this specification;
* authentication assumptions cannot be isolated safely;
* migration history is invalid;
* the test environment cannot provide meaningful tenant-isolation verification;
* a requested change would weaken security.

---

## Required completion report

Return:

1. final status;
2. implementation summary;
3. architecture decisions;
4. files created;
5. files modified;
6. database models added;
7. migration revision;
8. API routes added;
9. authorization rules implemented;
10. tenant-isolation mechanism;
11. background-context implementation;
12. audit implementation;
13. tests added;
14. exact commands executed;
15. exact command results;
16. migration upgrade result;
17. migration downgrade result;
18. Docker validation result;
19. security considerations;
20. known limitations;
21. unresolved issues;
22. Git diff summary;
23. current Git status.

Do not claim a command passed unless it was executed and its result was observed.
