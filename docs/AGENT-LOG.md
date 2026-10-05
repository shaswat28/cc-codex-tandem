# Agent log

Dated execution history. Appended by the lane runner; not read by default.

## 2026-10-05 — T-01, T-02

- Both implemented in isolated worktrees and merged to `main` after review.
- T-01 review finding: `classify` tested the usage-limit pattern before the exit
  code, so a successful job printing "rate limit" would be retried forever at
  unchanged effort. Fixed, with a regression test.
- T-02 review findings: `Outcome.BLOCKED` carried an uppercase value so
  `Outcome("blocked")` raised; a real failure retried with zero delay. Both fixed.
- Process finding: a status line written by searching forward for the next `TODO`
  landed on the wrong ticket when a concurrent writer had already updated the
  intended one. Folded into T-03's acceptance criteria.

## 2026-10-05 — T-03

- Added typed ticket parsing/rendering and ID-scoped status writes that check the
  expected state under an advisory writer lock. LF, CRLF and absent final newlines
  are preserved; malformed content raises typed errors.
- Review caught a body horizontal rule being mistaken for a separator; fixed and
  covered by regression tests. Concurrent writers are tested for stale-state rejection.
- Validation: `make check` passed on Python 3.13.9: lint, formatting, strict types,
  137 tests, 100% coverage. No git commands or real companion calls were run.

## 2026-10-05 — T-07

- Added an offline Node companion double, default pytest override and isolated
  workspace fixture. Seven scenarios, retry sequences, bounded foreground hangs,
  background status/result/cancel and optional file edits are documented in
  `tests/fakes/README.md`.
- Added 19 real Python/Node boundary tests. Review caught completed job state after
  a rejected edit; corrected it and asserted failed status. Traversal, absolute
  paths and directory/file symlink escapes are rejected before applying edits.
- Validation: `UV_CACHE_DIR=/tmp/cc-tandem-t07-uv-cache make check` passed on
  Python 3.13.9: lint, formatting, strict types, 189 tests, 100% Python coverage.
  The cache override avoids sandbox restrictions on the default UV cache. No git
  commands or real companion calls were run.
## 2026-10-05 — T-05

- Added `WorktreeManager(repo, base=..., worktree_root=...)` with create,
  commit, sync, merge and remove operations. Merge results identify the operation,
  ticket and conflict paths. Branches survive conflict and removal.
- The directory mutex lives in the common git directory so linked checkouts share
  it. Owner publication is atomic; a short advisory guard prevents contenders
  racing during stale-owner reclamation. Dead owners are reclaimed with a warning.
- Review strengthened cleanup around merge invocation, checked lane registrations,
  selected explicit branch refs to avoid tag ambiguity, and overrode no-commit and
  fast-forward-only preferences so successful merges finish their intended work.
- Validation: `make check` passed on Python 3.13.9: lint, formatting, strict types,
  217 tests (47 new real-git integration tests), 98.60% overall coverage and 96%
  worktree-module coverage. Tests cover conflict abort, interrupted-merge recovery,
  branch reuse, idempotent removal and concurrent merge/lock contention.
- Git operations ran only inside isolated temporary test repositories. No git
  commands targeted this project checkout; no real companion calls were made.
