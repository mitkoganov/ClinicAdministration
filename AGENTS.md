# AGENTS.md — shared rules for coding agents

These rules apply to every coding agent working in this repository (Claude
Code, Codex CLI, or any other). Claude-specific detail is in `CLAUDE.md`.

## Before editing

* Inspect the relevant existing code, tests, and docs before writing anything.
  Do not assume structure — read it.
* Confirm which module/task you are working on (see `tasks/current/task.md`).
  Do not start unscoped work.

## Scope discipline

* Make the minimal coherent change that satisfies the current task.
* Do not perform unrelated refactoring alongside a feature or fix.
* Do not add dependencies that are not required by the current task. Record
  exact versions in the relevant lock file.
* Do not weaken, skip, or delete a test to make a change pass. If a test is
  genuinely wrong, say so explicitly and let a human decide.

## Data & secrets

* Never access, request, print, or log secrets, credentials, or `.env`
  contents.
* Never create or use real patient data, in code, tests, fixtures, or
  examples. Synthetic data only.

## Git & deployment

* Never commit, push, merge, or deploy. Prepare changes; a human commits.
* Never run destructive git operations (`reset --hard`, `clean -f`, force
  push, branch deletion) without explicit human instruction.
* Schema changes go through an Alembic migration — never hand-edited against a
  running database.
* Behavioral changes require accompanying tests.

## Multi-tenant & authorization rules (apply once those modules exist)

* Tenant isolation must be enforced server-side, in the data access layer —
  never trust a client-supplied tenant identifier without validation.
* Authorization checks happen server-side. Never rely on the frontend or an
  LLM prompt to enforce access control.
* No sensitive healthcare data may appear in logs, error messages, or agent
  output.

## AI/LLM boundary

* LLM-driven components (once implemented) may only perform language
  understanding and response composition.
* An LLM must never directly execute a database write, booking, or scheduling
  action. It must call into deterministic, independently-authorized backend
  services.

## Reporting

* Report the exact commands run and their exit codes/output — never fabricate
  a result. If a command was not run, say so.
* If blocked (missing dependency, failing prerequisite, ambiguous
  requirement), stop and report rather than guessing or silently working
  around it.
