---
name: verify-codex
description: Verify lane results against actual checks, recorded evidence, user intent and the ticket before trusting completion claims.
---

# Verify Codex

Review the named ticket and completed lane result against the user's intended
behavior, repository instructions and actual evidence.
Boundary: this skill does not implement fixes, rewrite the runner or push changes.

Delegate deterministic checks to `cc_tandem.verify` from the target project:

- `rerun_checks(commands, cwd=repo)` runs the project's gate and each check claimed
  to pass. Compare actual exit codes, output and timeouts with the recorded claims.
- `scan_assistant_mentions(diff_or_paths, commit_messages=..., branch_names=...)`
  scans added lines and the result's commit and branch metadata. Review findings in
  context using the module's configurable allowlist for legitimate references.
- `verify_each_commit(revision_range, commands, repo=repo)` checks each result
  commit independently in disposable worktrees. Report the first failing commit;
  an empty range is no evidence of per-commit success.

Use the lane's recorded logs and evidence paths to inspect the actual artifacts;
missing evidence leaves the corresponding claim unverified. Do not replace any of
these operations with a prose summary of the job's reported success.

Make two separate judgments:

1. **Intent:** Is this the right behavior for the user's problem? Inspect the diff
   for defects and consider unspecified edge cases, even if every criterion passes.
2. **Conformance:** Does the result satisfy the ticket's behavior and acceptance
   criteria, supported by the rerun checks and inspected evidence?

Record defects in the ticket itself under that ticket in `BACKLOG.md`, separately
from implementation defects, so a faithfully implemented but wrong contract can
be corrected. Route fixes to a ticket and implementation lane rather than silently
patching during review.

Report actual check results, evidence inspected, intent and conformance findings,
and any remaining verification limits. Only after a clean review offer to push;
publication requires the user's authorization.
