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
   .venv\Scripts\python -m pytest
   ```
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

## Development workflow

See [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) for the rules that govern
AI-assisted development in this repository, and `.ai-workflow/` for the
implement → review → repair automation.

## Documentation

* [ARCHITECTURE.md](ARCHITECTURE.md) — modular monolith, planned modules, AI boundaries
* [SECURITY.md](SECURITY.md) — data handling, secrets, GDPR-oriented design
* [CONTRIBUTING.md](CONTRIBUTING.md) — local contribution workflow
