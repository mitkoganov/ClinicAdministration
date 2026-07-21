# CLAUDE.md — Claude Code implementation rules

Claude Code acts as the **implementation agent** in this repository. Codex CLI
is the independent, read-only reviewer (see `.ai-workflow/`). All rules in
`AGENTS.md` apply; this file adds Claude-specific operating rules.

## Role boundaries

* Claude Code implements changes on a feature branch (never on `main`/`master`
  directly — `.ai-workflow/scripts/common.ps1` enforces this).
* Claude Code does not review its own change as if it were an independent
  reviewer — that role belongs to the Codex CLI pass in `run-review.ps1`.
* Claude Code does not commit, push, merge, or deploy at any point in this
  workflow. It stops after implementation (or after a repair attempt) and
  waits for human review/commit.

## Repair loop

* When Codex review findings come back, Claude Code may attempt a repair.
* Maximum **two** repair attempts per review cycle
  (`.ai-workflow/config.json` → `maxRepairAttempts`). After two attempts, stop
  and hand back to the human — do not keep iterating silently.

## Command usage

* Before using any Claude Code CLI flag inside `.ai-workflow/scripts`, the
  script must have inspected `--help` for the locally installed version and
  used only supported flags. Do not assume flags from unrelated versions.

## Working directory discipline

* All work happens inside this repository. No command reads or writes outside
  the repository root, and no command reads `.env`.

## Task discipline

* Follow the active task in `tasks/current/task.md`. If the task is
  ambiguous or requires business decisions not yet made, stop and ask rather
  than inventing scope.
* This foundation stage explicitly excludes business functionality,
  authentication, and patient data — do not add any of these without an
  explicit, separate, human-approved task.
