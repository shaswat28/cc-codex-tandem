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

Status: TODO

---

## T-07: Fake Codex companion
Effort: medium

**Scope.** `tests/fakes/fake_companion.mjs` plus a pytest fixture that points
`CC_TANDEM_COMPANION` at it.

- Accepts the same arguments the real companion is invoked with and emits the same
  JSON shapes for `status --all --json`.
- Scriptable per test through an env var or a scenario file: succeed, fail, emit a
  usage-limit message, hang until killed, exit non-zero, produce invalid JSON, and
  "succeed but change nothing".
- Can simulate editing files in its working directory so merges have real content.

**Acceptance.** Each scenario has a test asserting the companion layer classifies it
correctly. The fake never reaches the network.

Status: TODO

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

Status: TODO

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

Status: TODO

---

## T-10: Skills
Effort: medium

**Scope.** `skills/ticket/SKILL.md`, `skills/tandem/SKILL.md`,
`skills/verify-codex/SKILL.md`.

- Thin: describe intent and delegate to `tandem`. No logic restated in prose.
- `/ticket` documents the split/diagnose/re-scope behaviour, the verbatim
  **Reported** block, the approval step for non-trivial tickets, `--raw`
  (`Status: UNREFINED`, never handed to Codex), and the `BLOCKED` escape hatch.
- A test asserts every skill has valid front matter with `name` and `description`,
  and that `claude plugin validate .` passes.

**Acceptance.** `claude plugin validate .` passes in CI. Front-matter test green.

Status: TODO

---

## T-11: Continuous integration
Effort: low

**Scope.** `.github/workflows/ci.yml`.

- macOS runner, Python 3.13, `uv sync`, then `make check`.
- Must exclude `live`-marked tests.
- Cache uv downloads. Run on push and pull request.

**Acceptance.** Workflow is green on `main`.

Status: TODO

---

## T-12: Classify failures before escalating effort
Effort: medium

**Scope.** `src/cc_tandem/effort.py` and `src/cc_tandem/companion.py`.

The current policy is binary: a usage limit retries at the same effort, anything
else escalates one level. That wastes quota, because most failures are not caused
by insufficient reasoning. A syntax error does not get fixed by thinking harder.

Replace `Outcome` with a failure taxonomy, classified from observable signals only
(exit code, output markers, whether the worktree diff is empty, whether the ticket
reached a terminal `Status:`). Do not attempt to infer intent from prose.

| Class | Signal | Policy |
| --- | --- | --- |
| `capacity` | usage/rate-limit markers on a non-zero exit | same effort, backoff, **does not consume an attempt** |
| `infrastructure` | companion/process error, no diff produced, no turn started | same effort, backoff |
| `implementation` | a diff exists and the project gate fails | same effort, **once**; escalate on a repeat |
| `stalled` | job ended with no diff and no block, or the same gate failure twice | escalate one level, capped at `high` |
| `owner_decision` | ticket set to `BLOCKED` | stop; **never** escalate |

`capacity` not consuming an attempt is the important change: a lane should not
exhaust its attempt budget waiting out a quota window.

**Acceptance.** Table-driven unit tests for every class and transition; a test that
`capacity` leaves the attempt count unchanged while every other class increments it;
a test that `owner_decision` never escalates under any attempt count; a test that
`implementation` escalates only on the second occurrence. Update `README.md`'s
effort section to document the taxonomy. Keep `xhigh` rejected.

Status: TODO
