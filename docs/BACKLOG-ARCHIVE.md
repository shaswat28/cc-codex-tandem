# Completed ticket details

## T-03: BACKLOG.md ticket parsing and writing
Effort: medium

**Scope.** `src/cc_tandem/backlog.py`. Read and write the format this file uses.

- Parse `## <id>: <title>`, the `Effort:` line, the body, and the `Status:` line.
- `set_status(id, status, note)` rewrites **only** that ticket's status line and
  leaves every other byte unchanged (verify by byte comparison in a test).
- `render_ticket(...)` writes a new ticket, with the reporter's words preserved
  verbatim in a `**Reported (verbatim):**` block quote and the interpretation below.
- `render_raw(...)` writes a ticket with the verbatim block, no interpretation, and
  `Status: UNREFINED`.
- Handle gracefully: missing ticket, duplicate ids, missing `Status:` line, a status
  value that is not recognised, CRLF input, and a ticket whose body contains a line
  starting with `Status:` inside a code fence (must not be mistaken for the real one).
- **`set_status` must locate the line by ticket id, never by searching forward for the
  next matching status text.** Observed failure: a status written "after T-01" landed
  on T-02 because T-01's line had already been updated by a concurrent writer. Raise a
  typed error when the named ticket has no status line, and make the caller pass the
  expected current status so a concurrent change is detected rather than overwritten.

**Acceptance.** Round-trip tests (parse → render → parse is stable); byte-level test
that `set_status` touches one line; each failure mode above raises a typed error or is
handled as specified; fixtures include a malformed file.

Status: DONE — Typed fence-aware parsing and rendering; byte-preserving status updates with expected-state checks and writer locking; 137 tests, 100% coverage.

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

Fixture configuration and supported commands: [fake companion guide](../tests/fakes/README.md).

Status: DONE — Offline scriptable companion and isolated fixtures cover all seven scenarios, file edits, retries and job status; 189 tests, 100% Python coverage.
## T-05: Git worktree lifecycle
Effort: high

**Scope.** `src/cc_tandem/worktree.py`. Real git, no mocks in the tests.

- `create(ticket, base)` adds a worktree on a new branch, reusing an existing branch
  if the ticket was attempted before.
- `commit_all(dir, message)`; no-op cleanly when there is nothing to commit.
- `merge_into_base(ticket)` takes a **lock** (directory mutex), merges `--no-ff`, and
  on conflict aborts the merge, keeps the branch and reports a typed conflict result.
  It must never leave the base repo mid-merge.
- `sync_base_into(ticket)` before merging; conflict handled the same way.
- `remove(ticket)` removes the worktree; tolerates an already-removed worktree.
- Stale lock detection: a lock whose owning pid is gone is reclaimed, with a log line.

**Acceptance.** Integration tests against real temporary repos: happy path; merge
conflict leaves base clean and branch intact; two concurrent merges serialise through
the lock (assert with threads); crash mid-merge (simulate by aborting) recovers;
stale lock reclaimed; worktree removal is idempotent; a ticket branch that already
exists is reused, not duplicated.

Status: DONE — Git worktree lifecycle with branch reuse, locked recoverable merges and typed conflicts; 47 real-git integration tests, green gate.

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

Status: DONE — Observable failure taxonomy, quota-safe attempt accounting, repeat-aware escalation and documented policy; 294 tests, green make check.
