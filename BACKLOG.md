# Backlog

Tickets are worked one per branch. Status values: `TODO`, `IN PROGRESS`, `DONE`,
`BLOCKED (needs owner decision)`, `UNREFINED`.

---

# Tickets

**Rules for every ticket.** Read `AGENTS.md` first and follow it. Work only inside
your own worktree. Python 3.13, `uv` for everything. Every ticket ships with tests;
`make check` must pass (ruff, `ruff format --check`, `mypy --strict`, pytest with a
90% coverage floor) before you set a ticket `DONE`. Type every function, including
tests. No assistant attribution anywhere, including branch names and comments. Do not
run git commands — the runner commits and merges. Do not call the real Codex
companion from a test. If a genuine owner decision with several valid answers is
required, record the options under the ticket, set `Status: BLOCKED (needs owner
decision)` and stop. When finished, set the ticket's `Status:` line to `DONE` with a
one-line outcome, and print a summary with test results.

---

## T-01: Locate and invoke the Codex companion
Effort: medium

**Scope.** `src/cc_tandem/companion.py`. Find `codex-companion.mjs` and run it.

- Search `~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`.
  Pick the **highest version** by semantic-version ordering, not string ordering
  (`1.0.10` beats `1.0.9`). Ignore directories that do not parse as a version.
- `CC_TANDEM_COMPANION` overrides the search with an explicit path.
- Raise a typed `CompanionNotFound` with a message naming what was searched when
  nothing matches; `CompanionInvalid` when the override path does not exist.
- `run(args) -> CompanionResult` (exit code, stdout, stderr, parsed JSON when the
  output is JSON). Never raise on a non-zero exit; return it.
- `status_all()` parses `status --all --json` into typed records. The observed shape
  has top-level `running` and `recent` lists of objects with `id`, `status` and an
  optional `summary`. Treat unknown fields as ignorable and missing lists as empty.
- Classify a result: `ok`, `usage_limit` (output matches a usage-limit message),
  `failed`. Keep the matcher a module-level constant so it is easy to update.
- `CC_TANDEM_DRY_RUN=1` returns a recorded no-op result instead of spawning.

**Acceptance.** Unit tests cover: version ordering including double-digit patch and
non-numeric directories; no installation found; override set and missing; JSON and
non-JSON output; non-zero exit; empty/absent `running`/`recent`; usage-limit
classification; dry-run spawns nothing (assert via a patched spawn).

Status: DONE — Typed companion discovery, invocation, status parsing, classification and dry-run; 42 tests, 100% coverage.

---

## T-02: Effort ladder policy
Effort: low

**Scope.** `src/cc_tandem/effort.py`. Pure functions, no I/O.

- `Effort` enum: `low`, `medium`, `high`. Parsing rejects `xhigh` with a message
  explaining it costs far more quota for little gain.
- `escalate(e)` → next level up, capped at `high` (`high` escalates to `high`).
- `for_difficulty(d)` maps `mechanical -> low`, `feature -> medium`,
  `architectural -> high`.
- `next_attempt(outcome, effort, attempt)` returns a decision:
  usage limit → retry at the **same** effort after a backoff; real failure → retry one
  level higher; success or `BLOCKED` → stop; attempts exhausted → stop.
- Backoff is exponential with a cap and is returned as seconds, not slept.

**Acceptance.** Unit tests for every transition in the table, `xhigh` rejection,
`high` capping, backoff growth and cap, and attempt exhaustion. Property test: the
effort returned is never below the one passed in.

Status: DONE — Typed effort ladder with xhigh rejected, capped escalation and exponential backoff on every retry; 50 tests, 100% coverage.

---

## T-03: BACKLOG.md ticket parsing and writing
Effort: medium

[Implementation details and acceptance criteria](docs/BACKLOG-ARCHIVE.md#t-03-backlogmd-ticket-parsing-and-writing).

Status: DONE — Typed fence-aware parsing and rendering; byte-preserving status updates with expected-state checks and writer locking; 137 tests, 100% coverage.

---

## T-04: Configuration
Effort: low

**Scope.** `src/cc_tandem/config.py`. Parse `.tandem.toml` with `tomllib`.

- Schema and defaults exactly as documented in `README.md`
  (`[codex]`, `[lanes]`, `[prompt]`, `[[queue]]`).
- Validate: `lanes.count >= 1`; `isolation` in `{worktree, none}`; every queue effort
  parses (so `xhigh` is rejected here too); queue is non-empty for `run`; unknown
  top-level keys are an error naming the key.
- Missing file produces a typed error that tells the user to create one, with the
  minimal example inline.

**Acceptance.** Unit tests for defaults, each validation failure with its message,
unknown keys, malformed TOML, and an empty queue.

Status: DONE — Typed TOML config with documented defaults, actionable validation and rejection of misspelled keys at every level; 122 tests, 100% coverage.

---

## T-05: Git worktree lifecycle
Effort: high

[Implementation details and acceptance criteria](docs/BACKLOG-ARCHIVE.md#t-05-git-worktree-lifecycle).

Status: DONE — Git worktree lifecycle with branch reuse, locked recoverable merges and typed conflicts; 47 real-git integration tests, green gate.

---

## T-06: Lane state machine and runner
Effort: high

**Scope.** `src/cc_tandem/lanes.py` and `src/cc_tandem/state.py`.

- A lane takes an ordered list of (ticket, effort) and runs them one at a time.
- Per ticket: skip if already `DONE`; create worktree; build the Codex prompt from
  `[prompt].read_first`, the ticket id and the rules block; invoke the companion;
  classify the outcome; apply the T-02 decision; on success commit, sync, merge,
  append to `docs/AGENT-LOG.md`, remove the worktree.
- `BLOCKED` or a merge conflict stops that lane and keeps the branch; other lanes
  continue.
- State is written to `.tandem/state.json` after every transition: per-ticket status,
  attempt count, current effort, timestamps, log paths. Writes are atomic
  (temp file then rename) so a reader never sees half a file.
- Resume: re-running reconciles the state file against live `status_all()` and against
  `BACKLOG.md`, and picks up where it stopped.
- A lane must never write outside its own worktree; assert the invariant and fail the
  lane if violated.

**Acceptance.** Integration tests using the fake companion from T-08: happy path
across two lanes; usage-limit then success; real failure escalates effort exactly
once per attempt; attempts exhausted; `BLOCKED` stops one lane and not the other;
merge conflict stops the lane; resume after a simulated crash; state file is always
valid JSON when read concurrently; a ticket already `DONE` is skipped.

Status: DONE — Lane state machine, atomic checkpointed state and resume; all nine acceptance scenarios covered by real-git integration tests. Coverage of lanes.py is 61% because the job was interrupted by a worktree collision, not because the work was too large; tracked by T-13.

---

## T-07: Fake Codex companion
Effort: medium

[Implementation details and acceptance criteria](docs/BACKLOG-ARCHIVE.md#t-07-fake-codex-companion).

Status: DONE — Offline scriptable companion and isolated fixtures cover all seven scenarios, file edits, retries and job status; 189 tests, 100% Python coverage.

---

## T-08: Command-line interface
Effort: medium

**Scope.** `src/cc_tandem/cli.py`, exposed as `tandem`.

- `tandem run` (detached by default; `--foreground` to stay attached), `tandem status`,
  `tandem stop`, `tandem doctor`.
- `run` detaches so it survives the caller exiting, writes logs under `.tandem/logs/`,
  and prints where to find them. Re-running while a run is active refuses with a
  clear message rather than starting a second runner.
- `status` reads the state file only. It must make **no** companion calls and must
  not block.
- `stop` cancels running jobs through the companion's own `cancel`, never `kill -9`.
- `doctor` checks: companion found and its version, Codex login, config valid and
  parsed, repo is a git repo and clean, base branch exists, `.gitattributes` has the
  union-merge entries, no stale lock, worktree dir writable. Prints a pass/fail table
  and exits non-zero if anything fails.
- Exit codes are documented and tested.

**Acceptance.** Tests for each subcommand including: `status` with no state file;
`run` refusing a second concurrent run; `stop` with nothing running; `doctor` failing
each individual check in isolation; detachment verified by asserting the child
survives the parent (integration test).

Status: DONE — Detached CLI with concurrent-run protection, offline status, cooperative cancellation and doctor checks; documented exit codes, 41 CLI tests, make check green (422 tests, 94.79% coverage).

---

## T-09: Verification procedure
Effort: medium

**Scope.** `src/cc_tandem/verify.py` — the scripted parts of `/verify-codex`.

- `rerun_checks(commands)` runs the project's own gate commands and returns actual
  results for comparison against claimed ones.
- `scan_assistant_mentions(diff_or_paths)` flags `claude`/`codex` in added lines,
  commit messages and branch names. It must not flag legitimate uses: a dependency
  named in a lock file, a URL, or this project's own name. Allowlist is configurable.
- `verify_each_commit(range)` checks out each commit in a throwaway worktree and runs
  the gate, reporting the first commit that fails.

**Review the behaviour against intent, not the output against the ticket.** The
ticket can itself be wrong, and a faithful implementation of a wrong ticket passes
every conformance check. Observed case: a ticket said "unknown top-level keys are an
error", the implementation did exactly that, and misspelled nested keys stayed
silently ignored. The checklist must prompt for "is this the right behaviour?"
separately from "does this match what was asked?", and record ticket defects back
into the backlog rather than only fixing the code.

**Acceptance.** Unit tests for the mention scanner's true and **false** positives
(this repo's own README and manifests must produce zero findings). Integration tests
for per-commit verification against a temporary repo with one deliberately broken
commit in the middle.

Status: DONE — Implemented gate reruns, configurable mention scanning, isolated per-commit verification and intent review; make check passes (265 tests).

---

## T-10: Skills
Effort: medium

**Scope.** `skills/ticket/SKILL.md`, `skills/tandem/SKILL.md`,
`skills/verify-codex/SKILL.md`.

- Thin: describe intent and delegate to `tandem`. No logic restated in prose.
- **The ticket is the handoff object, not the prompt.** The lane prompt `/tandem`
  sends Codex must stay compact: point at the ticket and the repository's own
  instructions, and let Codex read the code itself. Do not restate the ticket, and
  do not describe a solution — planning tokens spent producing a pseudo-patch are
  then spent again by Codex rediscovering it.
- **`/ticket` writes a contract, not an implementation.** Specify observable
  behaviour, edge cases, constraints and acceptance criteria. Name an interface only
  when another ticket depends on it: lanes run in separate worktrees and cannot
  negotiate, so a shared API has to be fixed centrally or the merges will not fit.
  Never prescribe how to implement something Codex can work out from the repository.
- Each skill states, in one line, what it does **not** do, so the boundary between
  Claude's judgement and Codex's implementation is explicit to a reader.
- `/ticket` documents the split/diagnose/re-scope behaviour, the verbatim
  **Reported** block, the approval step for non-trivial tickets, `--raw`
  (`Status: UNREFINED`, never handed to Codex), and the `BLOCKED` escape hatch.
- A test asserts every skill has valid front matter with `name` and `description`,
  and that `claude plugin validate .` passes.

**Acceptance.** `claude plugin validate .` passes in CI. Front-matter test green.

Status: DONE — Implemented three thin contract/execution/review skills, parsed front-matter tests and CI plugin validation; make check green (334 tests, 98.94% coverage).

---

## T-11: Continuous integration
Effort: low

**Scope.** `.github/workflows/ci.yml`.

- macOS runner, Python 3.13, `uv sync`, then `make check`.
- Must exclude `live`-marked tests.
- Cache uv downloads. Run on push and pull request.

**Acceptance.** Workflow is green on `main`.

Status: DONE — Workflow runs the full gate on a macOS runner with uv caching, excludes live tests, and has been green on main across consecutive runs.

---

## T-12: Classify failures before escalating effort
Effort: medium

[Implementation details and acceptance criteria](docs/BACKLOG-ARCHIVE.md#t-12-classify-failures-before-escalating-effort).

Status: DONE — Observable failure taxonomy, quota-safe attempt accounting, repeat-aware escalation and documented policy; 294 tests, green make check.

---

## T-13: Cover the lane runner and enforce coverage per module
Effort: medium

**Scope.** `tests/integration/test_lanes.py`, `tests/unit/`, `pyproject.toml`.

`lanes.py` sits at 61% statement coverage with 62 partial branches while the suite
reports 94% overall. A single global floor lets one weakly covered module hide
behind well covered ones, which is how an undertested state machine reaches `main`.

- Raise `lanes.py` to at least 90% statement **and** branch coverage. Target the
  uncovered paths rather than adding happy-path repeats: detachment, logging,
  reconciliation when the job list disagrees with the worktree, cleanup after a
  failed merge, invariant violations, and every early return.
- Add a **per-module** coverage floor so no single file can fall below 85% even when
  the total passes. Implement it as a check that reads coverage data and fails with
  the offending file names; wire it into `make check` and CI.
- Keep the existing global floor at 90%.

**Acceptance.** `lanes.py` at or above 90% statement and branch coverage; the
per-module check fails (proven by a test with a deliberately low threshold) and
passes on the real tree; `make check` and CI both run it.

Status: DONE — Runner statement and branch coverage at 100%; per-module floors enforced in make check and CI, with 450 tests passing.

---

## T-14: A lane must own its worktree exclusively
Effort: medium

**Scope.** `src/cc_tandem/lanes.py`, `src/cc_tandem/worktree.py`.

Observed on 2026-10-05: two jobs ran against the T-06 worktree at once because the
operator read `status --all` as "no job found" and relaunched. The second job saw
`lanes.py` changing underneath it and stopped to ask whether another agent was
editing. The first was then killed when the worktree was removed while it was live.
Neither was a failure of the ticket or the model; both were avoidable collisions.

- Take an **ownership lock** per ticket worktree, recording job id, pid and start
  time, held for the whole job. Starting a second job for a ticket that is already
  owned must fail immediately with the owning job id, not proceed.
- `remove()` must refuse to delete a worktree whose ownership lock is live, and say
  which job holds it. Forced removal requires an explicit override.
- Liveness comes from the lock, the worktree, and the presence of the job's
  `task-worker` process (`pgrep -f "task-worker.*--job-id <id>"`) — never from the
  companion's job list, which has been observed omitting live jobs and reporting
  "No job found" for jobs still running. Prefer the process check: it is the only
  source that was correct every time it was tested.

**Acceptance.** A test starting a second job for an owned ticket fails with the
owner's id; a test removing an owned worktree is refused and the worktree survives;
a stale lock whose pid is gone is reclaimed; the override path is tested.

Status: DONE — Exclusive job ownership, process-based liveness, stale-lock recovery and explicit removal override; 511 tests and make check pass.

---

## T-15: Treat "job ended asking a question" as its own outcome
Effort: medium

**Scope.** `src/cc_tandem/companion.py`, `src/cc_tandem/effort.py`, lane prompt.

Both T-06 jobs ended by posing a question with options and waiting. Detached, nobody
answers, so the job simply ends. Classified by the current taxonomy this looks like
`stalled` and escalates effort, which is wrong twice over: nothing was stalled, and
escalating reasoning cannot answer an environmental question.

- Add an `awaiting_input` class: the job produced a final message asking the operator
  a question rather than reaching a terminal ticket status. Detect it from the job's
  recorded final output, not from prose anywhere in the log.
- Policy: stop the lane, keep the branch, and surface the question verbatim. Never
  escalate effort and never consume further attempts.
- Extend the lane prompt: the `BLOCKED (needs owner decision)` protocol covers
  **product** decisions; for an environmental or operational surprise the job must
  record what it observed in the ticket and stop, rather than ask a question no one
  will read.

**Acceptance.** A fake-companion scenario ending with an unanswered question is
classified `awaiting_input`; a test asserts effort is unchanged and the attempt
budget is not consumed; the question text reaches the lane report.

Status: DONE — Recorded final operator questions stop the lane as awaiting_input, retain the branch and verbatim report, and preserve effort and attempts; 521 tests and make check green.

---

## T-16: Split a ticket that is genuinely too large
Effort: low

**Scope.** `skills/ticket/SKILL.md`, `skills/tandem/SKILL.md`, `README.md`.

Distinct from T-14 and T-15, which cover collisions and unanswered questions. A
ticket that repeatedly produces partial work *after* those causes are excluded is
too large for one job.

- When a ticket reaches the effort cap and genuinely stalls again, the lane stops and
  reports it as too large, naming the partial artefacts in the worktree. It must not
  retry a third time and must not split the ticket itself.
- Splitting is `/ticket`'s job: it reads the partial work, writes smaller tickets
  with the same contract style, and records the dependency order between them.
  Document the handback in both skills and beside the effort table in `README.md`.

**Acceptance.** A test driving genuine stalls at the capped effort asserts the lane
stops with a distinguishable "too large" outcome and keeps the branch.

Status: TODO

---

## T-17: A live fleet dashboard
Effort: high

**Scope.** `src/cc_tandem/ui/`, a new `tandem ui` subcommand.

Reference for the feel: [Scape Argus](https://www.scape.work/argus) — a dark
three-panel orchestrator view with mission-config cards, a live fleet of agent
sessions, and the backlog rendered as table, kanban or gantt. Borrow the shape and
the density, not the look; this is our own tool and should read as its own thing.

Everything it needs already exists. `.tandem/state.json` carries per-ticket phase,
attempts, effort, timestamps and log paths; `BACKLOG.md` carries the tickets;
`.tandem.toml` carries the configuration; the lane logs are on disk. **The UI is a
reader.** It must not drive Codex, mutate the backlog, or become a second source of
truth, and it must work when no run is active.

Three panels:

- **Config** — `.tandem.toml` as cards: model, effort default, lane count,
  isolation, base branch, and the queue in order. Read-only in v1.
- **Fleet** — one tile per lane: current ticket, phase, effort, attempt count,
  elapsed time, and liveness taken from the worker process, not a job list. A tile
  opens its lane log, tailed live.
- **Board** — tickets from `BACKLOG.md` as a table and a kanban by `Status:`.
  Gantt can wait. Clicking a ticket shows its body and, when it has run, its
  outcome, retained branch and evidence paths.

Constraints: serve on loopback only, bind to an ephemeral port, no external network
calls and no CDN assets — vendor anything needed. Dark and light must both work. It
has to degrade honestly: a missing state file means "no run yet", not an error page,
and a stale file must be labelled with its age rather than presented as live.

Plenty here is a judgement call — polling versus websockets, how much the board may
do, whether the whole thing should be a terminal UI instead of a browser one. Decide
the small ones; put real forks under the ticket as options and block rather than
guessing.

**Acceptance.** `tandem ui` serves the three panels against a fixture state file;
tests cover no-state, stale-state, a live run and a finished run; liveness comes
from the process check; no test or page makes a network request; the server refuses
a non-loopback bind.

Status: TODO

---

## T-18: Show Codex usage without leaving Claude
Effort: medium

**Scope.** `src/cc_tandem/usage.py`, `skills/codex-usage/SKILL.md`, a
`tandem usage` subcommand.

Delegating to Codex is only cheap if its cost is visible. Today there is no way to
see what a run consumed without leaving the session, and `codex` has no usage or
quota subcommand.

**The data is already on disk** (verified 2026-10-05):

- Rollouts live at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session-id>.jsonl`
  (73 present at the time of writing).
- Each turn records `payload.turn_token_usage` and `payload.usage` with
  `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
  `output_tokens`, `reasoning_output_tokens` and `total_tokens`.
- A lane job's Codex session id appears in `codex-companion status <job-id>` as
  "Codex session ID", and that id is in the rollout's filename. Attribution from
  ticket to job to rollout to token count is therefore possible, and was confirmed
  by hand: one job summed to 151,744 total tokens, 113,024 of it cached input.

**Trap to resolve first.** `payload.usage` and `payload.turn_token_usage` were
identical in a single-turn sample. Determine which is cumulative and which is
per-turn before summing anything, and cover it with a fixture rollout of at least
three turns. Summing a cumulative field would inflate every number this reports.

Requirements:

- `tandem usage` reports: this repository's lane jobs grouped by ticket, a total for
  a given day or date range, and a single job by id. Break out cached input, because
  a run that looks expensive by `total_tokens` may be mostly cache reads.
- A `/codex-usage` skill that answers "what has Codex cost me" in one call, with no
  arguments needed for the common case, and renders a compact table.
- Read **only** rollout files. `~/.codex/auth.json` and
  `.codex-global-state.json` hold credentials and resume tokens: never read, parse
  or print them. No network calls.
- Degrade honestly: no rollouts means "no Codex usage recorded", not an error. A
  session id with no matching rollout is reported as unattributed rather than
  dropped silently, so totals never quietly under-report.

**Out of scope, and say so in the output.** This reports *consumption* from local
rollouts, not *remaining plan allowance*. There is no local source for quota
headroom; the desktop app fetches that server-side. A line of output must make that
distinction clear so the number is not mistaken for a budget.

**Acceptance.** Fixture rollouts covering one turn, several turns, a malformed line
and an empty file; a test proving the cumulative-versus-per-turn question is settled
rather than assumed; attribution from job id through session id to totals; a test
asserting no credential file is opened and no network request is made; the
consumption-not-allowance caveat present in the rendered output.

Status: TODO
