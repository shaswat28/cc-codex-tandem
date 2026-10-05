# cc-codex-tandem

A [Claude Code](https://claude.com/claude-code) plugin for the **Claude plans, Codex builds** workflow.

Claude is good at reading a codebase, deciding what to do and judging whether the
result is right. Codex is good at grinding out the implementation. This plugin wires
the two together so the handoff is a written ticket instead of a copy-paste, and so
several tickets can run at once without stepping on each other.

```
  you ──▶ /ticket ──▶ BACKLOG.md ──▶ /tandem ──▶ ┌── lane A (git worktree) ── Codex
                                                 └── lane B (git worktree) ── Codex
                                                            │
                                      /verify-codex ◀────────┘
```

> **Status:** early. The command surface below is stable; internals are still moving.

## Why

If you already delegate implementation to Codex, you have probably written the same
shell script twice: launch a job, poll it, retry when you hit a usage limit, keep two
jobs from editing the same file, merge the result. This packages that, and fixes the
parts that bite:

- Codex jobs **outlive your shell**, so the runner detaches instead of dying with it.
- **Usage limits are not failures** — they are waited out and retried at the same effort.
- Each ticket gets its **own git worktree and branch**, so parallel jobs cannot collide.
- Status is read from a **state file**, not by polling in a loop that burns tokens.
- The companion binary is **located by version glob**, so a plugin update does not break you.

## Requirements

| | |
|---|---|
| OS | macOS (Linux untested) |
| [Codex CLI](https://github.com/openai/codex) | installed and logged in (`codex login status`) |
| Official Codex plugin | `codex@openai-codex`, installed in Claude Code |
| Python | 3.13+ with [uv](https://docs.astral.sh/uv/) |
| git | 2.40+ (worktree support) |

## Install

```bash
claude plugin marketplace add shaswat28/cc-codex-tandem
```

```bash
claude plugin install cc-codex-tandem
```

Restart Claude Code, then run `/tandem doctor` to check your setup.

## Commands

### `/ticket` — turn a thought into a Codex-ready ticket

Takes what you say, in whatever form you say it, and writes a self-contained ticket
into `BACKLOG.md`: scope, likely causes, acceptance criteria, effort tier and a
`Status:` line.

Your words are preserved verbatim at the top of every ticket, under **Reported**.
Everything below that is the assistant's interpretation and is labelled as such, so a
wrong reading is visible and correctable rather than silently baked in.

```
/ticket the settings screen flashes white for a frame when you go back to home
```

One message can become several tickets when it describes several problems.

**`/ticket --raw`** files what you said verbatim as `Status: UNREFINED` and stops.
Nothing is interpreted and the ticket is never handed to Codex until you work it up.
Use it to capture a thought before you lose it.

If writing the ticket would mean making a product decision that is genuinely yours,
the ticket is filed `Status: BLOCKED (needs owner decision)` with the options listed,
rather than the assistant quietly choosing for you.

### `/tandem` — run tickets through Codex in parallel lanes

```bash
/tandem run            # work the queue defined in .tandem.toml
/tandem status         # read the state file; no polling, no token burn
/tandem stop           # cancel running Codex jobs cleanly
/tandem doctor         # check login, config, worktrees, stale locks
```

Each ticket runs in its own git worktree on its own branch. When it finishes, the lane
commits, merges into the base branch under a lock and appends an entry to
`docs/AGENT-LOG.md`. A ticket that comes back `BLOCKED`, or a merge that conflicts,
stops that lane and keeps the branch for you to look at — it never force-merges.

### `/verify-codex` — check the work before you trust it

Re-runs the checks rather than reading Codex's summary of them.

1. Re-run every check the job claimed passed, and diff actual against claimed
2. Open the recorded evidence files and confirm they match those claims
3. Review the diff for defects
4. Scan the diff **and branch names** for assistant mentions that should not ship
5. Verify each commit independently in a throwaway worktree
6. Only then offer to push

## Configuration

Drop a `.tandem.toml` at the repo root. Everything has a default except the queue.

```toml
[codex]
model          = "gpt-6.1-sol"
effort_default = "medium"

[lanes]
count     = 2
isolation = "worktree"        # worktree | none
base      = "main"

[prompt]
# Files every Codex job is told to read before starting.
read_first = ["AGENTS.md", "CLAUDE.md"]

[[queue]]
ticket = "FB-14"
effort = "medium"

[[queue]]
ticket = "FB-15"
effort = "high"
```

### Effort

`low`, `medium` and `high` map to Codex reasoning effort. Pick by difficulty:
mechanical edits `low`, ordinary features `medium`, anything architectural `high`.

Failures are classified from exit codes, output markers, worktree changes, actual
project gate results and parsed ticket status. Prose claiming success or asking for
help does not decide the class.

| Class | Evidence | Retry policy |
| --- | --- | --- |
| `capacity` | Non-zero exit with usage/rate-limit markers | Same effort with backoff; consumes **no attempt** |
| `infrastructure` | Process error or no turn started, with no diff | Same effort with backoff |
| `implementation` | A diff exists and the project gate fails | Same effort once; escalate on a repeat |
| `stalled` | Job ends without completion or a block, including no diff; same gate failure twice | Escalate one level, capped at `high` |
| `owner_decision` | Ticket status is `BLOCKED (needs owner decision)` | Stop; never escalate |

A zero exit alone does not prove completion: the ticket must reach `DONE` without a
failing gate. A blocked ticket always stops, even if quota markers also appear.
All results except capacity consume an attempt; retries stop at the attempt limit.
Capacity is exempt from that budget but not unbounded: a lane gives up after 24
consecutive quota waits, so a quota that never reopens cannot retry forever.
Implementation occurrences are tracked separately, so infrastructure errors and
capacity waits cannot trigger a premature escalation. Gate failure identifiers
compare actual diagnostics, without interpreting prose. Retry delays grow
exponentially with consecutive waits and cap at one hour by default.

The pure `next_attempt` policy takes the count **before** the invocation (initially
zero), returns the updated `attempt` and `implementation_failures` counters, and
returns the delay without sleeping. It returns `backoff_count` too, so every counter it consumes is one it hands back. Callers supply `backoff_count` so
capacity waits grow their delay while preserving the attempt budget.
`xhigh` is rejected: it costs far more quota for little gain.

## Safety

- Codex jobs are **cancelled** through the companion, never `kill -9`, so its server
  state stays consistent.
- Lanes only ever write inside their own worktree; a job that escapes fails the lane.
- Nothing is pushed to a remote. Merging stops at your local base branch.
- `/tandem doctor` and `CC_TANDEM_DRY_RUN=1` let you see what would run without running it.

## Development

```bash
uv sync
make check          # ruff + mypy --strict + pytest with a 90% coverage floor
make install-hooks  # enable the in-repo commit-msg guard
```

Tests run against a **fake companion** that speaks the same CLI and JSON, so the full
lane state machine is covered without spending Codex quota. Tests marked `live` are
excluded by default and never run in CI.

## License

[AGPL-3.0-or-later](LICENSE).
