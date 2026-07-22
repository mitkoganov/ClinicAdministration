# Clinic Admin Platform

Production-oriented, multi-tenant administration platform for private clinics.

> Status: **foundation only**. No business functionality, authentication, or patient
> data handling has been implemented yet. See [ARCHITECTURE.md](ARCHITECTURE.md) for
> what is implemented vs. planned.

## Repository layout

```text
clinic-admin-platform/
├── backend/          FastAPI service (Python 3.13, SQLAlchemy 2, Alembic)
├── frontend/         Next.js App Router application (TypeScript, strict mode)
├── infra/            Infrastructure-related assets (compose overrides, configs)
├── .ai-workflow/      Local Claude Code / Codex CLI orchestration (no auto-commit)
├── .vscode/           Editor settings and Tasks
├── tasks/             Active and archived work tasks
├── reports/           Generated validation / review reports
├── docker-compose.yml Local development infrastructure
├── .env.example       Template for local environment variables
├── AGENTS.md          Shared rules for coding agents (Claude Code, Codex)
├── CLAUDE.md          Claude Code specific implementation rules
├── ARCHITECTURE.md    Current + planned architecture
├── SECURITY.md        Security and data-handling policy
└── CONTRIBUTING.md    Local contribution workflow
```

## Prerequisites

* Python 3.13 (project-local virtual environment in `backend/.venv`)
* Node.js LTS (v24.x) + npm
* Docker Desktop with the WSL2 backend
* PowerShell 7 (`pwsh`) recommended for the `.ai-workflow` scripts

## Local services and ports

| Service    | Port  | Purpose                        |
|------------|-------|---------------------------------|
| backend    | 8000  | FastAPI application             |
| frontend   | 3000  | Next.js development server      |
| postgres   | 5432  | Primary relational database     |
| redis      | 6379  | Cache / session store            |
| qdrant     | 6333  | Vector store (HTTP), 6334 (gRPC) |

## Getting started (local development)

1. Copy `.env.example` to `.env` and review every value — **do not** put real
   credentials or patient data in it. This step is manual and intentional.
2. Backend:
   ```powershell
   cd backend
   python -m venv .venv
   .venv\Scripts\python -m pip install -e ".[dev]"
   .venv\Scripts\python -m pytest -m "not integration"
   cd ..
   ```
   This runs only the unit tests — no PostgreSQL, no Docker, no database
   environment variables, no destructive-reset authorization required. See
   "Running tests" below for integration tests and the full quality gate.
3. Frontend:
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```
4. Infrastructure:
   ```powershell
   docker compose config
   docker compose up -d postgres redis qdrant
   ```

## Multi-tenant foundation (local API testing)

The backend now has a tenant/membership domain and tenant-scoped demo API
(`/api/v1/tenant-context`, `/api/v1/tenant-resources`), plus clinic and staff
administration (`/api/v1/clinic`, `/api/v1/clinic/staff`) — see
[ARCHITECTURE.md](ARCHITECTURE.md) → "Multi-tenancy" and "Clinic and staff
administration". Real login/session authentication now exists too (see
"Authentication (local testing)" below); the **development-only** identity
header provider described here remains available as a lower-friction way to
exercise tenant-scoped routes without creating an account, and is disabled
by default.

1. Enable it only in a `development` environment (`.env`):
   ```env
   ENVIRONMENT=development
   DEVELOPMENT_IDENTITY_ENABLED=true
   ```
   The application refuses to start if this is `true` outside
   `ENVIRONMENT=development`.
2. Every request to a tenant-scoped route must carry two headers:
   `X-Dev-User-Id: <uuid>` and `X-Tenant-Id: <uuid>`. These identify the
   caller and the tenant they're claiming to act in; the server always
   re-validates both against the database (tenant active, membership
   active) before granting access — the headers are a claim, not
   authorization.
3. Run migrations against your local Postgres (see "Local services and
   ports" above for connection details):
   ```powershell
   cd backend
   .venv\Scripts\python -m alembic upgrade head
   ```
   A `Tenant` and an active `TenantMembership` row must exist for the
   `X-Tenant-Id` / `X-Dev-User-Id` pair you use — this foundation stage has
   no tenant-provisioning UI or endpoint, so insert them directly (e.g. via
   `psql` or a short Python script using the app's models) before testing.
4. Run tests — see "Running tests" below for the two supported workflows.
5. To exercise the admin UI (`/settings/clinic`, `/settings/staff`) against
   this backend: start the frontend (`npm run dev` in `frontend/`), open
   either page, and enter the same `X-Dev-User-Id`/`X-Tenant-Id` pair from
   step 2 into the "Development identity" banner shown at the top — it is
   stored in the browser's `localStorage` only, never a security boundary,
   and is attached as headers to every API call the page makes.

## Authentication (local testing)

The backend now has production login/session authentication
(`/api/v1/auth/*`) — see [ARCHITECTURE.md](ARCHITECTURE.md) → "Authentication
and user identity" and [SECURITY.md](SECURITY.md) → "Authentication threat
model". This is independent of the development identity headers described
above; a real session always takes priority over a dev header when both are
present.

1. Run migrations (adds `user_accounts`, `auth_sessions`, `one_time_tokens`
   on top of the existing tenant/membership tables):
   ```powershell
   cd backend
   .venv\Scripts\python -m alembic upgrade head
   ```
2. This foundation stage has no account-provisioning UI or self-signup
   endpoint. Create a `UserAccount` directly (e.g. via a short Python
   script using `app.core.passwords.hash_password` and the app's models,
   the same way you'd insert a `Tenant`/`TenantMembership` row today) before
   testing login.
3. Start the backend and frontend, then open `/login` and sign in with that
   account's email/password. A successful login sets the session and CSRF
   cookies; `/settings/*` pages then use the session automatically
   (`frontend/app/lib/api.ts`) — no dev-identity headers are required once
   logged in.
4. Login is throttled per (email, client IP) via Redis
   (`LOGIN_RATE_LIMIT_MAX_ATTEMPTS`/`LOGIN_RATE_LIMIT_WINDOW_SECONDS` in
   `.env.example`) — make sure `docker compose up -d redis` is running, or
   rate limiting fails open (allows the request) rather than blocking login
   entirely; see `SECURITY.md`.
5. Password reset (`/forgot-password`, `/reset-password`) and invitation
   acceptance (`/invitations/accept`) issue and validate tokens
   server-side, but **no email is sent** — there is no delivery mechanism
   in this foundation stage. To test either flow locally, read the raw
   token directly from the `one_time_tokens` table (or a debug log line
   you add temporarily and remove before committing) and build the URL by
   hand, e.g. `http://localhost:3000/reset-password?token=<raw token>`.

## Running tests

Tests are split into two pytest markers: unit tests (no database
required) and `integration` tests (require a real, disposable, explicitly
authorized Postgres test database — see `backend/tests/db_safety.py`).
**No script ever derives or falls back to a test database automatically —
a test database must always be explicitly configured and authorized.**

### Unit checks (no Postgres required)

```powershell
cd backend
.venv\Scripts\python -m pytest -m "not integration"
```

Ruff and mypy also require no database:

```powershell
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy app
```

### Full local quality gate (starts its own disposable test database)

```powershell
pwsh -NoProfile -File .ai-workflow\scripts\run-tests.ps1
```

This runs ruff, mypy, and the unit tests directly, then runs the
integration tests via `.ai-workflow\scripts\run-local-integration-tests.ps1`
— a dedicated wrapper that:

1. starts (or verifies) the repository's own `postgres-test` Compose
   service (`docker compose --profile test up -d postgres-test` —
   a separate container, `tmpfs`-backed, sharing no volume with the normal
   `postgres` service, defined in `docker-compose.yml`);
2. independently verifies the running container really is that dedicated
   service (checks its `POSTGRES_DB`, and that the resulting URL is not
   equal to `DATABASE_URL`) before doing anything destructive;
3. sets `TEST_DATABASE_URL`/`ALLOW_DESTRUCTIVE_TEST_DB_RESET=true` **only**
   for that pytest child process;
4. runs the integration tests, then stops `postgres-test` (pass
   `-KeepRunning` to leave it up for a follow-up run).

It never touches the normal `postgres` service or its data. Run it
directly if you only want the integration suite:

```powershell
pwsh -NoProfile -File .ai-workflow\scripts\run-local-integration-tests.ps1
```

### CI / explicit configuration

Set both variables explicitly before invoking either pytest or
`run-tests.ps1`, and the generic scripts will use exactly what you set
(without ever deriving or falling back to anything else):

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@<host>:<port>/<a database whose name contains 'test'>"
$env:ALLOW_DESTRUCTIVE_TEST_DB_RESET = "true"
cd backend
.venv\Scripts\python -m pytest -m integration
```

`backend/tests/db_safety.py` refuses to run destructive setup — before
opening any database connection — unless **both** of the following hold:
* `TEST_DATABASE_URL`'s database name ends with `_test` or contains
  `test`, and it does not resolve to the same host/port/database as
  `DATABASE_URL`;
* `ALLOW_DESTRUCTIVE_TEST_DB_RESET=true` is set explicitly — by you, by CI,
  or by `run-local-integration-tests.ps1` after its own verification above.
  No script ever sets this automatically or falls back from
  `TEST_DATABASE_URL` to `DATABASE_URL`.

## Development workflow

See [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) for the rules that govern
AI-assisted development in this repository, and `.ai-workflow/` for the
implement → review → repair automation.

## Documentation

* [ARCHITECTURE.md](ARCHITECTURE.md) — modular monolith, planned modules, AI boundaries
* [SECURITY.md](SECURITY.md) — data handling, secrets, GDPR-oriented design
* [CONTRIBUTING.md](CONTRIBUTING.md) — local contribution workflow
