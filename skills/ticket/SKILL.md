---
name: ticket
description: Turn a report or request into a reviewable behavior contract in BACKLOG.md, or capture it verbatim with --raw.
---

# Ticket

Write the handoff object in `BACKLOG.md`; the implementing lane reads it directly.
Boundary: this skill does not implement changes or launch implementation jobs.

Read the repository's instructions and backlog rules. Preserve the user's words,
including whitespace and line breaks, in a **Reported (verbatim):** block quote at
the top of each ticket. Label everything inferred below it **Interpretation:**;
keep hypotheses distinct from observed facts.

For `--raw`, use `cc_tandem.backlog.render_raw` to file only the report with
`Status: UNREFINED`, then stop. Do not diagnose, refine, queue or hand it to an
implementation lane until it has been refined and approved where required.

For a refined ticket:

- Split independent problems into separate tickets. Preserve the original report
  on each and identify the part each interpretation addresses.
- Diagnose enough to identify the observable problem and affected scope; inspect
  relevant code when useful. If the diagnosis changes the proposed scope, re-scope
  the contract explicitly so the user can see that change.
- Specify observable behavior, edge cases, constraints and verifiable acceptance
  criteria. Do not prescribe algorithms, file-by-file edits or a pseudo-patch that
  the implementer can derive from the repository. Fix a shared interface centrally
  only when another ticket depends on it; separate lanes cannot negotiate an API.
- Choose effort through `cc_tandem.effort.for_difficulty`. Use
  `cc_tandem.backlog.render_ticket` for the standard format, retaining the project's
  ticket ID convention and recording dependencies when tickets must run in order.
- Present non-trivial contracts for approval before filing them as runnable `TODO`
  tickets or adding them to a queue. Existing explicit approval of that contract
  suffices. Routine, unambiguous tickets can be filed directly.

When a genuine owner decision has several valid answers, file the options under
the affected ticket with `Status: BLOCKED (needs owner decision)` and stop its
refinement. Do not choose silently or send that ticket to a lane.

Use `cc_tandem.backlog` for parsing, rendering and status updates. Identify updates
by ticket ID and supply the expected current status to `set_status`; preserve other
tickets. Leave execution to `/tandem`.
