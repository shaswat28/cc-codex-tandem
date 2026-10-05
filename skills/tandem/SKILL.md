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

Report retained branches and blockers from the runner. Use `/verify-codex` to
review completed work before offering any publication action.
