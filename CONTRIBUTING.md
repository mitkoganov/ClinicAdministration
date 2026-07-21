# Contributing (local workflow)

This project is currently in local, single-developer foundation stage. There
is no remote repository and no CI configured yet.

## Setup

See [README.md](README.md) "Getting started".

## Before committing

1. Confirm `git config user.name` and `git config user.email` are set
   (repository-local or global) — commits are never made with a missing
   identity.
2. Run the quality gates:
   * Backend: `ruff check`, `mypy`, `pytest`
   * Frontend: `npm run lint`, `npm run typecheck`, `npm run build`
3. Review `git status` and `git diff --stat` — no `.env`, credentials, or
   patient-like data should appear.

## Branching

* `main` is protected: implementation changes are made on a feature branch,
  never committed directly to `main`/`master` (enforced by
  `.ai-workflow/scripts/common.ps1`).

## AI-assisted changes

* Claude Code implements on a feature branch.
* Codex CLI reviews read-only — it never writes to the repository.
* No AI agent commits, pushes, merges, or deploys. A human reviews and commits.

See `AGENTS.md` and `CLAUDE.md` for the full rule set, and `.ai-workflow/` for
the implement → review → repair scripts and VS Code tasks.
