# Security Policy

This document describes the security posture of the **foundation** stage of
this repository. It will be revisited before any real health-data processing
begins.

## Data handling

* No real patient data may be entered into this repository, its databases, its
  seed data, its tests, or any local environment at any stage of foundation
  development.
* All local development data must be synthetic.
* `.env` is never committed. `.env.example` documents required variable names
  only, with placeholder (non-functional) values.

## Secrets

* Secrets are supplied exclusively through environment variables, loaded via
  Pydantic Settings in the backend.
* No secret is ever logged, printed, or included in error responses.
* No secret is read or displayed by AI coding agents operating in this
  repository (see `AGENTS.md`).

## Access control (planned, once auth/tenants modules exist)

* Authorization decisions are made **server-side**, never trusted from client
  input.
* Tenant isolation is enforced in the data access layer for every tenant-scoped
  query — a missing or forged tenant identifier must fail closed.
* Principle of least privilege applies to database roles, service accounts,
  and any future API keys.

## Logging

* Logging is structured (not free-text patient data).
* Logs must never contain patient-identifying information, credentials, or
  full request/response bodies once real data exists.
* Redaction is applied at the logging boundary, not left to callers to
  remember.

## Audit

* Once the `audit` module exists, every state-changing business action must
  produce an audit record capturing actor, tenant, action, and timestamp.

## AI-specific restrictions

* LLM components are restricted to language understanding and response
  composition only — see "AI boundary" in `ARCHITECTURE.md`.
* An LLM must never be permitted to make a diagnostic or clinical decision.
* An LLM must never directly execute a database write or scheduling action;
  all actions go through deterministic, independently-authorized services.

## Dependency maintenance

* Dependencies are pinned via lock files (`requirements`/`uv.lock` or
  equivalent for backend, `package-lock.json` for frontend).
* Vulnerability warnings from `pip`, `npm audit`, or Dependabot-equivalent
  tooling are not suppressed; they are triaged.

## GDPR-oriented design

* Data minimization: only fields required for a specific, scoped feature are
  modeled — no speculative "just in case" personal fields.
* Right to erasure and data portability are architectural concerns for the
  `tenants`/`practitioners`/`conversations` modules once implemented, not an
  afterthought.

## Backups & incident response (planned)

* Production backup strategy, retention, and restore testing must be defined
  before any production deployment — out of scope for the foundation stage.
* An incident response runbook must exist before real patient data is
  processed.

## Threat modeling

* A formal threat model must be produced and reviewed before this platform
  processes any real health data. The foundation stage intentionally excludes
  authentication and data processing to keep the attack surface minimal until
  that review happens.

## Vulnerability reporting

* During local foundation development, report issues by opening a task in
  `tasks/` describing the issue, impact, and suggested remediation. A formal
  external reporting channel will be defined before any deployment.
