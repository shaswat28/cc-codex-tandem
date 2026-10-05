# cc-codex-tandem — project instructions

## Purpose

A Claude Code plugin for the "Claude plans, Codex builds" workflow. Three commands:
`/ticket` (write a Codex-ready ticket), `/tandem` (run tickets through Codex in
isolated parallel lanes), `/verify-codex` (check the work before trusting it).

**This repository is public.** Never commit anything about private or employer work:
no client names, internal hostnames, real ticket IDs from other projects, or paths
that reveal them. Examples and fixtures use neutral, invented names.

## Architecture

| Path | What it is |
| --- | --- |
| `.claude-plugin/` | Plugin manifest and single-plugin marketplace manifest |
| `skills/<name>/SKILL.md` | The three user-facing commands |
| `src/cc_tandem/` | All logic. Entry point `cli.py` exposes `tandem` |
| `tests/unit/` | Pure logic: effort ladder, parsing, path resolution |
| `tests/integration/` | Real git worktrees driven against the fake companion |
| `tests/fakes/` | Fake `codex-companion` speaking the same CLI and JSON |

Skills stay thin: they explain intent and call `tandem`. Logic that can be tested
lives in Python, not in Markdown and not in shell.

## Build and test

```bash
uv sync
make check          # ruff + ruff format --check + mypy --strict + pytest --cov (floor 90%)
make install-hooks  # enable .githooks/commit-msg
```

- Tests marked `live` need a real Codex login and burn quota. They are excluded from
  `make check` and must never run in CI.
- Everything else runs against `tests/fakes/fake_companion.mjs`. Never call the real
  companion from an automated test.

## Workflow rules

- Branch per ticket, named for the ticket (`T-03`). Base branch is `main`.
- One concern per commit, with tests and types updated in the same commit.
- **No assistant attribution anywhere** — not in commits, PR bodies, code, docs or
  branch names. `.githooks/commit-msg` enforces the commit case.
- Never push to a remote from inside the tool's own lane logic.
- Tickets live in `BACKLOG.md`; dated execution history in `docs/AGENT-LOG.md`.

## Standing decisions

- **Python, not shell.** The lane runner was previously a ~200-line bash script; it is
  being rewritten in Python because worktree/merge/retry logic needs real tests.
- **Companion located by version glob**, never a pinned version path.
- **Usage limits are not failures.** Wait and retry at the same effort level.
- **`xhigh` effort is rejected** — far more quota for little gain.
- **Detach by default.** Long runs must survive the 30-minute foreground tool limit.
- **No polling loops inside a session.** The runner writes state; the session reads it.
- **Tickets specify behaviour, interfaces and acceptance; not implementation.** Codex
  can read the repository, so prescribing a solution wastes planning tokens and has it
  rediscover the same thing. The exception is a **shared interface**: lanes run in
  separate worktrees and cannot negotiate, so any API two tickets both depend on is
  named centrally in the ticket, or their merges will not fit together.
- **One job per worktree, always.** Confirm ownership before starting a job and
  before removing a worktree. Two jobs in one worktree stop each other, and a
  worktree removed under a live job destroys its work.
- **The aggregate job list under-reports.** `status --all --json` has been observed
  returning an empty `running` list while a job was live. Reconcile against per-job
  status and the worktree, never the aggregate alone.

## Current state

- Merged to `main`: T-01 companion, T-02 effort, T-03 backlog, T-04 config,
  T-05 worktree, T-07 fake companion, T-09 verify, T-12 failure taxonomy.
  332 tests, gate green.
- Remaining: T-06 lane runner (high), T-08 CLI, T-10 skills, T-11 CI.
- T-10 is the highest-risk ticket: its prompts decide whether planning and
  implementation duplicate each other. Review it closely.
- Review after a green gate has found defects in six of eight tickets so far.
- Blocked: nothing.
