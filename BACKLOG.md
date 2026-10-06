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

---

## T-19: Request JSON when launching a background task
Effort: medium

**Reported (verbatim):**

> Runner._submit launches task --background without --json but only accepts a parsed JSON jobId. Installed companion 1.0.6 handleTask emits renderQueuedTaskLaunch text unless --json is supplied. The fake always emits JSON.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Bug.

**Scope.** `src/cc_tandem/lanes.py::_submit; tests/fakes/fake_companion.mjs`.

**Required behavior.** A successful launch must publish the real job ID and ownership before polling. A malformed launch response must stop safely rather than automatically start another job whose predecessor may still be live.

**Acceptance.** Use an offline companion that emits text by default and JSON only on request. One queued ticket launches exactly once, records its job ID and reaches completion. Exercise successful-but-unparseable submission and prove no duplicate launch occurs.

Status: TODO

---

## T-20: Address status, result and cancellation in the job worktree
Effort: medium

**Reported (verbatim):**

> Submission supplies --cwd for the ticket, but Runner._await_job and cli._cancel omit it. Companion 1.0.6 job-control resolves jobs using the command cwd; state is keyed by a hash of the canonical workspace root. Linked worktrees have different roots. --repo also does not change the Python caller cwd.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Bug.

**Scope.** `src/cc_tandem/lanes.py::_await_job; src/cc_tandem/cli.py::_cancel, _Worker._call`.

**Required behavior.** All job operations must use the same workspace context as submission, including resume, stop and the stop-during-submission race. A command invoked outside the target repository must behave identically.

**Acceptance.** An offline fixture stores jobs separately by canonical cwd. Launch from another directory, poll and retrieve from the correct worktree, resume after coordinator death, and cancel the correct job. Jobs in other repositories are untouched.

Status: TODO

---

## T-21: Validate the companion contract and perform a controlled live smoke run
Effort: high

**Reported (verbatim):**

> Every automated runner test uses the fake. It accepts unknown flags, returns JSON without --json, and shares one job store across worktrees. Those differences conceal T-19 and T-20. Detachment integration replaces Runner.run, so it does not establish live end-to-end correctness.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Validation gap.

**Scope.** `tests/fakes/; tests/integration/; README.md`.

**Required behavior.** Cover the supported companion CLI and response shapes with offline contract fixtures, including launch failures, queued/running/terminal jobs, result errors and cancellation. Separately provide an explicitly invoked live smoke procedure against a disposable neutral repository, with evidence of launch, verification, merge, audit and cleanup. Never run live jobs in CI or the default gate.

**Acceptance.** Offline regressions fail on the current missing-JSON and wrong-cwd behavior. The opt-in live procedure records actual job IDs, outcomes and checks and reports failure honestly; release readiness requires a successful recorded run. No automated offline test invokes the real companion.

Status: TODO

---

## T-22: Honor stop requests through verification and publication
Effort: high

**Reported (verbatim):**

> Stop is observed only in _Worker._call and retry sleep. A temporary-repository reproduction set stop.request before _finish(COMMITTING): the runner merged the ticket, reported done and removed its worktree. During WAITING, job_id still names the previous finished job, but _cancel asks to cancel it; the real companion rejects finished jobs.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Bug.

**Scope.** `src/cc_tandem/cli.py::_Worker, _stop, _cancel; src/cc_tandem/lanes.py::_evaluate, _publish`.

**Required behavior.** A stop must prevent new submissions and further publication at safe transition boundaries, retain unfinished work, and not report a completed historical job as a cancellation failure. Stop remains cooperative and repeated requests are safe. Describe the outcome if a merge was already committed before stop arrived.

**Acceptance.** Exercise stop during gates, before commit, before merge, between audit transitions and during retry wait. No subsequent ticket launches or unpublished branch merges; unfinished worktrees survive. Repeated stop and already-terminal jobs do not fail spuriously.

Status: TODO

---

## T-23: Terminate gate descendants when verification times out
Effort: high

**Reported (verbatim):**

> verify.rerun_checks uses subprocess.run with shell=True for string gates. Timeout terminates the immediate process, not its descendants. An offline reproduction using a Python child in the background plus shell wait returned timed_out=True while that child subsequently wrote a marker file.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Bug / process safety.

**Scope.** `src/cc_tandem/verify.py::rerun_checks, verify_each_commit`.

**Required behavior.** A timed-out gate must leave no gate descendant running against the lane. Preserve partial diagnostics, bound shutdown time, and do not remove a verification worktree or start a retry while a previous gate can still mutate it.

**Acceptance.** A shell gate spawns a child that would write after the deadline; timeout prevents that write and proves the descendant exited. Cover argument-list gates, nested children, successful commands and per-commit worktree cleanup without affecting unrelated processes.

Status: TODO

---

## T-24: Bound companion control calls and retain uncertain live jobs
Effort: high

**Reported (verbatim):**

> companion.run has no subprocess timeout. Runner._call holds the shared companion lock during each call. One hung submission or status/result request can block every lane and make cooperative stop ineffective.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Reliability / process safety.

**Scope.** `src/cc_tandem/companion.py::run; src/cc_tandem/lanes.py::_call, _submit`.

**Required behavior.** Give control operations bounded, observable failure behavior. A submission timeout must be treated as uncertain execution rather than permission to duplicate the job. Preserve ownership and recovery evidence whenever a worker may have started. Cancellation must also return a bounded diagnostic.

**Acceptance.** Offline fixtures hang during launch, status, result and cancel. Calls return within a configured bound; unrelated lanes can make progress; no duplicate submission follows uncertain launch; locks and branches remain recoverable. Do not impose a short control timeout on the background implementation itself.

Status: TODO

---

## T-25: Keep exclusive worktree ownership through validation and cleanup
Effort: high

**Reported (verbatim):**

> OwnershipLock is held only inside _submit/_await_job. It is released before _evaluate, gate execution, commit and merge. Another process can acquire ownership of the same worktree during validation or publication; remove can also see it as unowned. Runtime run/launch locks are checkout-local while job ownership is shared through git common-dir.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Concurrency bug.

**Scope.** `src/cc_tandem/lanes.py::_submit, _evaluate, _publish; src/cc_tandem/worktree.py::OwnershipLock`.

**Required behavior.** Reserve a ticket worktree for the entire lifecycle that reads, verifies or mutates it, through a coordinated cleanup. External submissions and removal must refuse while the coordinator is validating or publishing. Resume must reclaim only a proven stale reservation, including across linked checkouts.

**Acceptance.** Pause a gate or publication step and attempt a second ownership claim and removal from another process: both refuse and the checkout survives. Prove normal cleanup, dead-coordinator recovery and worker-outliving-coordinator behavior still work.

Status: TODO

---

## T-26: Bind merge approval to the exact verified revisions
Effort: high

**Reported (verbatim):**

> sync_base_into and merge_into_base acquire and release the common git mutex independently; the gate runs between them. Another publisher can change base between verification and merge. MERGING checkpoints store no verified revision, and resume calls merge directly, even if a clean ticket branch was changed during downtime.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Integrity bug.

**Scope.** `src/cc_tandem/lanes.py::_publish; src/cc_tandem/state.py::TicketState; src/cc_tandem/worktree.py::sync_base_into, merge_into_base`.

**Required behavior.** Publish only a combined revision whose lane and base inputs match recorded successful verification. Detect intervening base/branch changes and revalidate or stop safely. Resume must not treat a historical phase name as proof that the current tree passed.

**Acceptance.** Use two publisher processes and advance base after one gate passes; an unverified combination cannot merge. Pause at MERGING, change and commit the lane, then resume: it must verify that new revision or retain it. Record the verified revisions in durable evidence.

Status: TODO

---

## T-27: Validate backlog semantics after merges before publishing
Effort: medium

**Reported (verbatim):**

> BACKLOG.md uses merge=union. A three-way offline merge of TODO versus DONE and IN PROGRESS returned git exit 0 but duplicated Status lines; backlog.parse rejected it. _publish checks only the project gate after sync and does not parse the merged backlog or recheck the completed ticket. An arbitrary project gate need not validate this metadata.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Data integrity bug.

**Scope.** `.gitattributes; src/cc_tandem/lanes.py::_publish; src/cc_tandem/backlog.py`.

**Required behavior.** A syntactically successful git merge must not publish duplicate or conflicting ticket metadata. Preserve unrelated ticket edits, require the implemented ticket to remain DONE, and surface ambiguous backlog reconciliation with the branch retained.

**Acceptance.** Real-git tests cover conflicting status edits, concurrent ticket-body edits, adjacent independent tickets and duplicate IDs. Invalid merged metadata never reaches base or a DONE checkpoint; independent valid edits survive.

Status: TODO

---

## T-28: Classify quota exhaustion from failure evidence rather than arbitrary prose
Effort: medium

**Reported (verbatim):**

> classify searches all stdout/stderr, including serialized rawOutput and summaries, for usage-limit phrases before gate failure classification. Reproduction: an unrelated failed job whose rawOutput says "Implemented rate limit handling; unrelated test failed" is capacity, despite a failing gate. It consumes no attempts and can retry repeatedly.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Bug.

**Scope.** `src/cc_tandem/companion.py::classify; src/cc_tandem/lanes.py::_await_job`.

**Required behavior.** Actual quota failures retain same-effort retry behavior; incidental phrases in code, implementation summaries or test output must not override unrelated failures. Retain the relevant structured error evidence and distinguish uncertain classifications honestly.

**Acceptance.** Fixtures cover genuine companion quota failures, unrelated failures discussing rate limits, successful rate-limiter work, partial output followed by quota failure and BLOCKED precedence. False capacity classifications consume the appropriate failure budget.

Status: TODO

---

## T-29: Provide safe recovery for stopped and interrupted checkpoints
Effort: high

**Reported (verbatim):**

> FAILED, BLOCKED, CONFLICT and EXHAUSTED checkpoints are never retried by Runner. SUBMITTING without job_id requests manual reconciliation. README exposes only run/status/stop/doctor and no safe way to reset a resolved checkpoint; users must edit internal JSON, and deleting state can discard ownership evidence.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Missing feature.

**Scope.** `src/cc_tandem/cli.py; src/cc_tandem/lanes.py::_run_ticket; src/cc_tandem/state.py; README.md`.

**Required behavior.** Provide a documented recovery operation that inspects a named ticket, retained branch and live ownership, explains the retained evidence, and resumes only after the blocker is resolved. Support adoption of an identified interrupted job without duplicate launch. Preserve other ticket records and logs.

**Acceptance.** Recover from an operator-resolved block, conflict, exhausted retry, cooperative stop and interrupted submission. Refuse recovery while another coordinator owns the ticket or an unidentified worker may be live. Other lanes and completed ticket history remain intact.

Status: TODO

---

## T-30: Make marketplace installation provide a usable Python runtime
Effort: medium

**Reported (verbatim):**

> README Install only adds the marketplace/plugin and instructs /tandem doctor. The plugin manifests supply skills, with no runtime setup; the skill runs tandem from the target repository. The Python console script exists only after a separate package installation, and /ticket and /verify-codex also require cc_tandem imports.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Packaging gap.

**Scope.** `README.md::Install; .claude-plugin/; skills/; pyproject.toml`.

**Required behavior.** Document and support a clean-machine installation path that makes the CLI and Python helpers available from arbitrary target repositories. A missing runtime must produce actionable setup instructions. Keep the installed tool environment separate from the target project environment.

**Acceptance.** In an isolated environment without this checkout editable-installed or on PATH/PYTHONPATH, follow the published installation steps and run the doctor command plus backlog/verification helpers from a neutral repository. No development checkout is implicitly required.

Status: TODO

---

## T-31: Allow projects to configure their actual verification gates
Effort: medium

**Reported (verbatim):**

> Runner defaults to make check and supports checks only as a Python constructor argument. The CLI and TOML schema offer no gate configuration. Projects with another gate cannot use the documented workflow without adding a synthetic Makefile or bypassing the CLI.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Missing feature.

**Scope.** `src/cc_tandem/config.py; src/cc_tandem/cli.py::_execute; src/cc_tandem/lanes.py; README.md`.

**Required behavior.** Expose explicit project gate commands and deadlines through validated configuration, retaining the current default for compatibility. Use the same configured gate for initial verification and verification after base sync, and show it in diagnostics and recorded results.

**Acceptance.** A project without a Makefile completes using its configured gate. Multiple gates run in order with failures retained; invalid/empty gate configuration fails before submission; the default existing configuration still uses make check. Document command trust and execution context.

Status: TODO

---

## T-32: Prepare lane environments before launching sandboxed jobs
Effort: medium

**Reported (verbatim):**

> Root instructions require warming each worktree .venv and UV_CACHE_DIR=.uv-cache because a sandboxed job cannot write uv default cache. Runner.create is followed immediately by task submission: no environment preparation or per-job cache setup is implemented. New git worktrees do not inherit ignored .venv directories.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Missing behavior.

**Scope.** `src/cc_tandem/lanes.py::_run_ticket, _submit; src/cc_tandem/config.py; AGENTS.md::Build and test`.

**Required behavior.** Support a documented project preparation step and lane-local environment settings before submission. For this repository, a fresh lane must have its dependencies ready and use a cache contained in the lane. Preparation failures stop before spending a model turn and retain actionable diagnostics.

**Acceptance.** From a fresh temporary worktree with no .venv or warm user cache, prove the configured preparation completes and the submitted job receives its lane-local cache setting. Test preparation failure, resume without redundant destructive setup and independent lane environments.

Status: TODO

---

## T-33: Fail preflight on unsupported isolation and the wrong base checkout
Effort: medium

**Reported (verbatim):**

> config.load accepts isolation=none and doctor can pass it, while Runner immediately rejects it. Doctor checks that the configured base ref exists but not that it is checked out. Runner also defers that checkout check until publication, so it can spend implementation quota before discovering that it cannot merge.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Bug.

**Scope.** `src/cc_tandem/cli.py::_doctor, _run; src/cc_tandem/config.py::load; src/cc_tandem/lanes.py::Runner`.

**Required behavior.** Doctor and run must agree on executable configuration. Validate the supported isolation, current base checkout, runnable ticket metadata, queue uniqueness and contained read-first paths before launching jobs. Report concrete corrective actions without changing the user checkout.

**Acceptance.** Doctor and run fail early for isolation=none and a non-base checkout even when base exists. Invalid queue/path cases produce no task calls or ticket worktree creation. Supported valid configuration continues to pass.

Status: TODO

---

## T-34: Reset the consecutive capacity-wait counter after other outcomes
Effort: low

**Reported (verbatim):**

> next_attempt sets waits = backoff_count + (result == CAPACITY). A non-capacity result preserves the previous counter rather than clearing it. Reproduction: capacity followed by infrastructure leaves backoff_count=1. The documented 24 consecutive quota waits therefore count separated capacity episodes together.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Bug.

**Scope.** `src/cc_tandem/effort.py::next_attempt; README.md::Effort`.

**Required behavior.** The consecutive capacity counter and capacity backoff must reset after a non-capacity outcome while preserving the independently defined implementation/attempt counters. Clarify any separate policy for non-capacity retry delay.

**Acceptance.** Table tests cover capacity -> infrastructure -> capacity and capacity -> implementation -> capacity, plus uninterrupted capacity exhaustion. Separated waits start a fresh capacity streak; the configured consecutive limit still bounds continuous quota waiting.

Status: TODO

---

## T-35: Support dependency-aware scheduling of queued tickets
Effort: high

**Reported (verbatim):**

> QueueEntry carries only ticket and effort, and Runner distributes entries round-robin. An ordered queue does not make the second ticket wait for the first when there are multiple lanes. T-16 asks split tickets to record dependency order, but execution has no dependency contract.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Feature proposal.

**Scope.** `src/cc_tandem/config.py::QueueEntry; src/cc_tandem/lanes.py::run; BACKLOG.md::T-16`.

**Required behavior.** Allow explicit ticket dependencies and start a dependent ticket only after all prerequisites are successfully published to base. Unrelated tickets remain parallel. Dependency failures or owner blocks must surface as waiting/blocking reasons rather than letting dependents implement against incomplete interfaces.

**Acceptance.** A two-lane test proves dependent B does not start before A is merged and B sees A changes. Test cycles, unknown dependencies, failed/blocked prerequisites, already-DONE prerequisites and resume. Existing queues without dependencies retain current scheduling.

Status: TODO

---

## T-36: Make dry-run a useful non-mutating execution preview
Effort: medium

**Reported (verbatim):**

> README says CC_TANDEM_DRY_RUN=1 shows what would run. In Runner, state and ticket worktrees/branches are created first; only _submit detects the no-op response and raises "Dry run cannot execute or complete a lane", leaving FAILED state and a retained worktree. It provides no queue/prompt preview.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Bug / feature gap.

**Scope.** `src/cc_tandem/companion.py::run; src/cc_tandem/lanes.py::_run_ticket, _submit; README.md::Safety`.

**Required behavior.** A dry-run must validate and show the planned tickets, efforts, worktree destinations and commands without submitting jobs, creating branches/worktrees, writing checkpoints or poisoning later real execution. Clearly distinguish a preview from completed work.

**Acceptance.** Snapshot repository refs, worktrees and runtime files before and after a CLI dry-run: they are identical. No companion process starts, the preview is actionable, and a subsequent real offline run proceeds without manually repairing a FAILED checkpoint.

Status: TODO

---

## T-37: Define and enforce the verification write boundary
Effort: high

**Reported (verbatim):**

> README promises lanes only write inside their worktree. Worker prompts and final symlink inspection are not enforcement for gates: verify.rerun_checks executes the lane modified commands and project scripts directly under the host process, without a sandbox. Gate descendants can write outside cwd. This is a local execution trust boundary, not evidence of a remotely reachable exploit.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P1. **Kind:** Security hardening.

**Scope.** `src/cc_tandem/verify.py::rerun_checks; src/cc_tandem/lanes.py::_evaluate, _publish; README.md::Safety`.

**Required behavior.** State explicitly which repository code and generated changes are trusted, and enforce the promised filesystem boundary during automated verification or clearly narrow that promise to the boundary actually enforced. Protect unrelated checkouts and runtime/credential locations while allowing necessary declared build resources; never validate isolation by reading actual credentials.

**Acceptance.** Use harmless temporary sentinel files outside a fixture lane to test attempted out-of-lane writes, including through symlinks and children. The supported constrained mode prevents those writes and ordinary gates still run; documentation and diagnostics honestly describe any intentionally unrestricted mode and its trust requirement.

Status: TODO

---

## T-38: Publish backlog status updates atomically for readers and crash recovery
Effort: medium

**Reported (verbatim):**

> backlog.set_status locks the backlog inode, then seeks to zero, rewrites and truncates the entire file in place. backlog.read takes no lock. A concurrent reader can see partial contents, and an interrupted write can leave the backlog corrupted despite the writer-lock guarantee. StateStore already uses atomic replacement for this reason.

**Interpretation:**

**Written by Codex.** Review date: 2026-10-05.

**Priority:** P2. **Kind:** Data integrity hardening.

**Scope.** `src/cc_tandem/backlog.py::set_status, read; tests/unit/test_backlog.py`.

**Required behavior.** Readers must observe a complete old or new backlog, and an interrupted status update must not destroy unrelated tickets. Preserve byte layout and expected-status checks. Cooperating writers must remain serialized even if publication replaces the backlog inode.

**Acceptance.** Concurrent reader/writer tests never observe invalid or truncated tickets. Fault injection during publication preserves the last complete version. Two competing writers still enforce expected_status correctly, and CRLF/untouched ticket bytes retain existing guarantees.

Status: TODO
