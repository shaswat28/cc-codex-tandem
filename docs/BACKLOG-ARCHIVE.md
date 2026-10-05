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

