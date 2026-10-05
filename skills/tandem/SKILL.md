---
name: tandem
description: Run approved backlog tickets through isolated lanes, or inspect, stop and diagnose a tandem run.
---

# Tandem

Delegate execution to the `tandem` CLI from the target repository root.
Boundary: this skill does not re-plan tickets, implement code or push changes.

Read the repository's instructions and `.tandem.toml`. Before running, ensure the
queue contains refined, approved tickets where approval is required; never hand
`UNREFINED` or `BLOCKED (needs owner decision)` tickets to an implementation lane.
Missing or unclear contracts go back to `/ticket`.

Map the requested operation directly to the CLI:

| Request | Command |
| --- | --- |
| Run the configured queue (default when no operation is given) | `tandem run` |
| Explicitly stay attached | `tandem run --foreground` |
| Read current state once | `tandem status` |
| Cancel the run | `tandem stop` |
| Diagnose setup | `tandem doctor` |

Let the CLI own isolation, job lifecycle, retry policy, commits, merges and state.
Return its result and reported log paths. After a detached launch, return control;
read status only when requested, without session polling loops. If the command is
unavailable or fails, report the diagnostic rather than recreating the runner in
shell or invoking the companion directly.

The ticket is the handoff object, not the prompt. Let the runner build its compact
lane prompt from the ticket ID, configured `read_first` files and repository rules.
Do not copy the ticket body into it or add a proposed solution. Its instruction is
simply to read the repository instructions and named ticket in `BACKLOG.md`, then
own implementation, testing and debugging to the ticket's completion or documented
owner-decision block.

## Never run two jobs against one worktree

A ticket's worktree has exactly one owner at a time. Before starting work on a
ticket, confirm no job already owns it; if one does, leave it alone.

- **A job is live until the worktree says otherwise.** Do not infer that a job has
  died from the companion's aggregate job list. It has been observed returning an
  empty running list for a live job, omitting a live job entirely, and answering
  "No job found" for a job that was still working. Query the specific job id, and
  trust the worktree's ownership lock and its file mtimes over any of it.
- **Never relaunch a ticket to "restart" it.** A duplicate job lands in the same
  worktree, sees files changing underneath it, and stops. If you believe a job is
  gone, prove it from the lock and the worktree before starting anything.
- **Never remove a worktree you have not proven is unowned.** Removing one under a
  live job destroys its work mid-write. Clean up only after the owning job has
  reached a terminal state, and never with a force flag to get past a refusal.

## A question is not a failure

A detached job that ends by asking the operator something has not failed and is not
stalled: nobody was there to answer. Surface the question, keep the branch, and
decide. Do not relaunch it, do not raise effort, and do not count it as an attempt.

Partial work after an interrupted job is salvage, not evidence that the ticket was
too large. Rule that out first: a ticket goes back to `/ticket` for splitting only
once collisions and unanswered questions have been excluded.

Report retained branches and blockers from the runner. Use `/verify-codex` to
review completed work before offering any publication action.
