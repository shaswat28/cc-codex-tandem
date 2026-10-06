"""Ordered, isolated lanes with recoverable job and merge checkpoints.

``Runner.run`` is the worker entry point (a CLI may detach this worker). Threads
run lanes independently; only the coordinator writes shared state/runtime logs.
Job polling happens here, never in a status-reading session. A known job is always
queried directly on resume because aggregate status can omit running jobs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import TypedDict, Unpack

from cc_tandem import backlog, companion, verify
from cc_tandem.backlog import Status, Ticket
from cc_tandem.companion import CompanionResult
from cc_tandem.config import Config, QueueEntry
from cc_tandem.effort import Effort, FailureClass, next_attempt
from cc_tandem.state import Phase, StateStore, TicketState, timestamp
from cc_tandem.worktree import DirectoryMutex, WorktreeManager, worker_alive

_STOPPED = {Phase.BLOCKED, Phase.CONFLICT, Phase.EXHAUSTED, Phase.FAILED}
_COMPLETE = {Phase.DONE, Phase.SKIPPED}
_FINISHING = {
    Phase.COMMITTING,
    Phase.SYNCING,
    Phase.MERGING,
    Phase.LOGGING,
    Phase.SYNCING_LOG,
    Phase.MERGING_LOG,
    Phase.CLEANUP,
}
_ACTIVE = {"queued", "running", "pending", "in_progress"}
_TERMINAL = {"completed", "succeeded", "failed", "cancelled", "canceled"}


class Transition(TypedDict, total=False):
    attempts: int
    effort: Effort
    implementation_failures: int
    backoff_count: int
    worktree: str | None
    job_id: str | None
    retry_at: str | None
    finished_at: str | None
    gate_failure: str | None
    logs: tuple[str, ...]
    error: str | None


class LaneError(RuntimeError):
    """A lane cannot proceed without risking isolation or duplicate execution."""


def inside(root: Path, relative: str) -> Path:
    """Assert containment before a lane reads or writes a repository path."""
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise LaneError(f"Lane path escapes its worktree: {relative}")
    return path


def build_prompt(ticket: Ticket, read_first: Sequence[str], rules: str) -> str:
    """Name the shared interface and rules; let the worker choose implementation."""
    return (
        f"Read these files first, in order: {', '.join(read_first) or '(none)'}.\n"
        f"Implement ticket {ticket.id} from BACKLOG.md, section Tickets.\n"
        "Own implementation, testing and debugging through completion.\n"
        "Work only inside this worktree. Run no git commands.\n"
        "Locate the ticket's Status line by its own heading.\n"
        "If a product decision is needed, record options under that ticket and mark it\n"
        "BLOCKED (needs owner decision). For an environmental or operational surprise,\n"
        "first try to work around it; if you cannot, record what you observed under\n"
        "the ticket and stop. Never end by asking a question: this job is detached\n"
        "and nobody will answer. Otherwise pass the project gate before DONE.\n\n"
        f"{rules}\n"
    )


def _rules(text: str) -> str:
    marker = "**Rules for every ticket.**"
    start = text.find(marker)
    if start < 0:
        raise LaneError("BACKLOG.md has no Rules for every ticket block")
    block = text[start:]
    end = block.find("\n---")
    heading = block.find("\n## ")
    stops = [offset for offset in (end, heading) if offset >= 0]
    return block[: min(stops)] if stops else block.rstrip()


def _git_output(path: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise LaneError(f"Cannot inspect lane changes: {process.stderr.strip()}")
    return process.stdout


def _diff(path: Path) -> bool:
    return bool(_git_output(path, "status", "--porcelain"))


class Runner:
    """Run a config queue round-robin, or explicit ordered lanes of QueueEntry.

    Only worktree isolation is supported: accepting ``none`` would break this
    runner's isolation contract. Stopped lanes remain stopped on resume; resolved
    owner decisions/conflicts require the owner to reset the affected checkpoint.
    Completed base tickets override stale checkpoints and finish pending cleanup.
    """

    def __init__(
        self,
        repo: Path,
        config: Config,
        *,
        manager: WorktreeManager | None = None,
        store: StateStore | None = None,
        checks: Sequence[verify.Command] = ("make check",),
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 2.0,
        max_attempts: int = 3,
        max_capacity_waits: int = 24,
        backoff_seconds: int = 60,
        backoff_cap_seconds: int = 3600,
    ) -> None:
        if config.lanes.isolation != "worktree":
            raise LaneError("Lane runner requires worktree isolation")
        if not checks or poll_seconds <= 0:
            raise ValueError("Checks must be nonempty and poll_seconds must be positive")
        # Validate the retry policy before starting jobs or creating worktrees.
        next_attempt(
            FailureClass.OK,
            config.codex.effort_default,
            0,
            max_attempts=max_attempts,
            max_capacity_waits=max_capacity_waits,
            backoff_seconds=backoff_seconds,
            backoff_cap_seconds=backoff_cap_seconds,
        )
        self.repo = repo.resolve()
        self.config = config
        self.manager = manager or WorktreeManager(self.repo, base=config.lanes.base)
        if self.manager.repo != self.repo:
            raise LaneError("Worktree manager belongs to a different coordinator checkout")
        self.store = store or StateStore(self.repo)
        if self.store.repo != self.repo:
            raise LaneError("State store belongs to a different coordinator checkout")
        self.checks = checks
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.max_capacity_waits = max_capacity_waits
        self.backoff_seconds = backoff_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        self._companion_lock = Lock()
        self._publish_lock = Lock()

    def _call(self, args: Sequence[str]) -> CompanionResult:
        # Launch/query calls are short. Serialise them without serialising job work.
        with self._companion_lock:
            return companion.run(args)

    def _change(
        self, record: TicketState, phase: Phase, **changes: Unpack[Transition]
    ) -> TicketState:
        return self.store.put(replace(record, phase=phase, **changes))

    def _ticket(self, path: Path, ticket: str) -> Ticket:
        return backlog.get_ticket(backlog.read(inside(path, "BACKLOG.md")), ticket)

    def _checkout(self, record: TicketState) -> Path:
        expected = self.manager.path_for(record.ticket)
        if record.worktree is None or Path(record.worktree) != expected or expected == self.repo:
            raise LaneError("Checkpoint worktree does not belong to this ticket")
        if expected.resolve() != expected or not expected.is_dir():
            raise LaneError("Lane worktree is missing or has become a symlink")
        # A symlink must not turn a repo-relative worker edit into an outside write.
        # Ignore build environments: their interpreter links legitimately point
        # outside the checkout. Check every tracked/unignored worker artifact.
        names = _git_output(
            expected, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        )
        for name in names.split("\0"):
            if not name:
                continue
            path = expected / name
            if path.is_symlink() and not path.resolve().is_relative_to(expected):
                raise LaneError(f"Lane symlink escapes its worktree: {path.relative_to(expected)}")
        return expected

    def run(self, lanes: Sequence[Sequence[QueueEntry]] | None = None) -> dict[str, TicketState]:
        if lanes is None:
            lanes = [
                self.config.queue[index :: self.config.lanes.count]
                for index in range(self.config.lanes.count)
            ]
        entries = [entry for lane in lanes for entry in lane]
        if len({entry.ticket for entry in entries}) != len(entries):
            raise LaneError("A ticket cannot be queued in more than one lane")
        for entry in entries:
            self.manager.path_for(entry.ticket)
            self._ticket(self.repo, entry.ticket)
        # The worker owns the run lock. Status readers only read state.json.
        self.store._contained(self.store.path.parent / "run.lock")
        self.store._contained(self.store.path.parent / "run.lock.guard")
        with DirectoryMutex(self.store.path.parent / "run.lock", timeout=0).acquire():
            existing = self.store.read()
            for lane, queue in enumerate(lanes):
                for entry in queue:
                    saved = existing.get(entry.ticket)
                    if saved is not None and saved.lane != lane:
                        raise LaneError(
                            f"Cannot move checkpoint {entry.ticket} to a different lane"
                        )
                    if saved is None:
                        now = timestamp()
                        self.store.put(TicketState(entry.ticket, lane, entry.effort, now, now))
            with ThreadPoolExecutor(max_workers=max(1, len(lanes))) as executor:
                futures = [executor.submit(self._lane, queue) for queue in lanes]
                for future in futures:
                    future.result()
        return self.store.read()

    def _lane(self, queue: Sequence[QueueEntry]) -> None:
        for entry in queue:
            record = self.store.read()[entry.ticket]
            try:
                record = self._run_ticket(record)
            except (OSError, ValueError, RuntimeError) as exc:
                record = self.store.read()[entry.ticket]
                record = self._change(record, Phase.FAILED, error=str(exc), finished_at=timestamp())
            if record.phase in _STOPPED:
                break

    def _run_ticket(self, record: TicketState) -> TicketState:
        base_ticket = self._ticket(self.repo, record.ticket)
        if base_ticket.status == Status.DONE:
            if record.phase in _COMPLETE:
                return record
            if record.phase == Phase.RUNNING:
                with self.manager.ownership(record.ticket).acquire(record.job_id):
                    self._await_job(record)
            # After a merge crash, do not discard the still-pending audit entry.
            if record.phase in _FINISHING:
                return self._finish(record)
            if record.worktree is not None:
                self._checkout(record)
                self.manager.remove(record.ticket)
            return self._change(record, Phase.SKIPPED, finished_at=timestamp())
        if base_ticket.status == Status.BLOCKED:
            return self._change(record, Phase.BLOCKED, finished_at=timestamp())
        if base_ticket.status == Status.UNREFINED:
            raise LaneError("UNREFINED tickets must be refined before running")
        if record.phase in _COMPLETE:
            raise LaneError(
                "Completed checkpoint disagrees with base backlog; reset before rerunning"
            )
        if record.phase in _STOPPED:
            return record
        if record.phase in _FINISHING:
            return self._finish(record)
        if record.phase in {Phase.PENDING, Phase.PREPARING}:
            record = self._change(record, Phase.PREPARING)
            path = self.manager.create(record.ticket)
            record = self._change(record, Phase.PREPARING, worktree=str(path))
        path = self._checkout(record)
        if record.phase == Phase.SUBMITTING and record.job_id is None:
            # A launch may have succeeded before the process died saving its ID.
            raise LaneError(
                "Interrupted submission without job ID; reconcile manually before retry"
            )
        while True:
            ticket = self._ticket(path, record.ticket)
            if ticket.status == Status.BLOCKED and record.phase != Phase.RUNNING:
                return self._change(record, Phase.BLOCKED, finished_at=timestamp())
            if record.phase == Phase.WAITING:
                if record.retry_at is None:
                    raise LaneError("Waiting checkpoint has no retry deadline")
                remaining = (
                    datetime.fromisoformat(record.retry_at) - datetime.now(UTC)
                ).total_seconds()
                if remaining > 0:
                    self.sleep(remaining)
                record = self._change(record, Phase.PREPARING, retry_at=None, job_id=None)
            if record.phase != Phase.RUNNING:
                if ticket.status == Status.DONE:
                    record = self._evaluate(record, CompanionResult(0, "Recovered DONE", ""))
                    if record.phase in _FINISHING:
                        return self._finish(record)
                    if record.phase in _STOPPED:
                        return record
                    continue
                for name in self.config.prompt.read_first:
                    inside(path, name)
                prompt = build_prompt(
                    ticket,
                    self.config.prompt.read_first,
                    _rules(inside(path, "BACKLOG.md").read_text(encoding="utf-8")),
                )
                record, result = self._submit(record, path, prompt)
            else:
                with self.manager.ownership(record.ticket).acquire(record.job_id):
                    result = self._await_job(record)
            record = self._evaluate(record, result)
            if record.phase in _FINISHING:
                return self._finish(record)
            if record.phase in _STOPPED:
                return record

    def _submit(
        self, record: TicketState, path: Path, prompt: str
    ) -> tuple[TicketState, CompanionResult]:
        ownership = self.manager.ownership(record.ticket)
        with ownership.acquire() as owner:
            record = self._change(record, Phase.SUBMITTING, job_id=None)
            launched = self._call(
                [
                    "task",
                    "--write",
                    "--fresh",
                    "--background",
                    "--cwd",
                    str(path),
                    "--model",
                    self.config.codex.model,
                    "--effort",
                    record.effort,
                    prompt,
                ]
            )
            if launched.dry_run:
                raise LaneError("Dry run cannot execute or complete a lane")
            job = launched.json.get("jobId") if isinstance(launched.json, dict) else None
            if launched.exit_code or not isinstance(job, str) or not job:
                return record, launched
            ownership.set_job(owner, job)
            record = self._change(record, Phase.RUNNING, job_id=job)
            return record, self._await_job(record)

    def _await_job(self, record: TicketState) -> CompanionResult:
        if record.job_id is None:
            raise LaneError("Running checkpoint has no job ID")
        while True:
            result = self._call(["status", record.job_id, "--json"])
            job = result.json.get("job") if isinstance(result.json, dict) else None
            if worker_alive(record.job_id):
                self.sleep(self.poll_seconds)
                continue
            if result.exit_code or not isinstance(job, dict):
                raise LaneError(
                    f"Cannot reconcile job {record.job_id}: {result.stdout} {result.stderr}"
                )
            status = job.get("status")
            if status in _ACTIVE:
                self.sleep(self.poll_seconds)
                continue
            if status not in _TERMINAL:
                raise LaneError(f"Unknown job status for {record.job_id}: {status}")
            output = self._call(["result", record.job_id, "--json"])
            if output.exit_code:
                raise LaneError(f"Cannot retrieve job {record.job_id}: {output.stderr}")
            summary = job.get("summary", "")
            return replace(
                output,
                exit_code=0 if status in {"completed", "succeeded"} else 1,
                stdout=f"{output.stdout}\n{summary}",
            )

    def _evaluate(self, record: TicketState, result: CompanionResult) -> TicketState:
        path = self._checkout(record)
        ticket = self._ticket(path, record.ticket)
        changed = _diff(path)
        checks = (
            verify.rerun_checks(self.checks, cwd=path)
            if (ticket.status != Status.BLOCKED and (changed or ticket.status == Status.DONE))
            else ()
        )
        passed = all(check.passed for check in checks) if checks else None
        failure = None
        if passed is False:
            # Stable command + diagnostics distinguish recurring observable failures.
            diagnostics = [
                (check.command, check.stdout, check.stderr) for check in checks if not check.passed
            ]
            failure = hashlib.sha256(json.dumps(diagnostics).encode()).hexdigest()
        outcome = companion.classify(
            result,
            has_diff=changed,
            gate_passed=passed,
            ticket_status=ticket.status,
            gate_failure=failure,
            previous_gate_failure=record.gate_failure,
            turn_started=record.job_id is not None or not result.process_error,
        )
        text = json.dumps(
            {
                "outcome": outcome,
                "effort": record.effort,
                "job_id": record.job_id,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "checks": [asdict(check) for check in checks],
                "question": companion.awaiting_question(result)
                if outcome == FailureClass.AWAITING_INPUT
                else None,
            },
            indent=2,
        )
        log = self.store.write_log(f"{record.ticket}-{len(record.logs) + 1}.json", text + "\n")
        decision = next_attempt(
            outcome,
            record.effort,
            record.attempts,
            implementation_failures=record.implementation_failures,
            backoff_count=record.backoff_count,
            max_attempts=self.max_attempts,
            max_capacity_waits=self.max_capacity_waits,
            backoff_seconds=self.backoff_seconds,
            backoff_cap_seconds=self.backoff_cap_seconds,
        )
        if outcome != FailureClass.OK and ticket.status == Status.DONE:
            backlog.set_status(
                inside(path, "BACKLOG.md"),
                record.ticket,
                Status.IN_PROGRESS,
                "Runner verification failed; see recorded gate diagnostics",
                expected_status=Status.DONE,
            )
        phase = (
            Phase.COMMITTING
            if outcome == FailureClass.OK
            else Phase.BLOCKED
            if outcome in {FailureClass.OWNER_DECISION, FailureClass.AWAITING_INPUT}
            else Phase.WAITING
            if decision.retry
            else Phase.EXHAUSTED
        )
        deadline = (datetime.now(UTC) + timedelta(seconds=decision.backoff_seconds)).isoformat()
        return self._change(
            record,
            phase,
            attempts=decision.attempt,
            effort=decision.effort,
            implementation_failures=decision.implementation_failures,
            backoff_count=decision.backoff_count,
            retry_at=deadline if decision.retry else None,
            gate_failure=failure,
            logs=(*record.logs, log),
            error=companion.awaiting_question(result)
            if outcome == FailureClass.AWAITING_INPUT
            else record.error,
            finished_at=timestamp() if phase in _STOPPED else None,
        )

    def _finish(self, record: TicketState) -> TicketState:
        # Keep sync, verification and merge together across this runner's lanes.
        with self._publish_lock:
            return self._publish(record)

    def _publish(self, record: TicketState) -> TicketState:
        if record.phase == Phase.CLEANUP:
            self.manager.remove(record.ticket)
            return self._change(record, Phase.DONE, finished_at=timestamp())
        path = self._checkout(record)
        while True:
            phase = Phase(record.phase)
            if phase == Phase.COMMITTING:
                self.manager.commit_all(path, f"Implement {record.ticket}")
                record = self._change(record, Phase.SYNCING)
            elif phase in {Phase.SYNCING, Phase.SYNCING_LOG}:
                merged = self.manager.sync_base_into(record.ticket)
                if merged.status == "conflict":
                    return self._change(
                        record, Phase.CONFLICT, error=str(merged.conflicts), finished_at=timestamp()
                    )
                # Sync can introduce new code: verify the combined checkout too.
                checks = verify.rerun_checks(self.checks, cwd=path)
                if not all(check.passed for check in checks):
                    raise LaneError("Project gate failed after syncing base; branch retained")
                record = self._change(
                    record, Phase.MERGING if record.phase == Phase.SYNCING else Phase.MERGING_LOG
                )
            elif phase in {Phase.MERGING, Phase.MERGING_LOG}:
                merged = self.manager.merge_into_base(record.ticket)
                if merged.status == "conflict":
                    return self._change(
                        record, Phase.CONFLICT, error=str(merged.conflicts), finished_at=timestamp()
                    )
                record = self._change(
                    record, Phase.LOGGING if record.phase == Phase.MERGING else Phase.CLEANUP
                )
            elif phase == Phase.LOGGING:
                audit = inside(path, "docs/AGENT-LOG.md")
                audit.parent.mkdir(parents=True, exist_ok=True)
                marker = f"<!-- tandem:{record.ticket}:{record.created_at} -->"
                content = audit.read_text(encoding="utf-8") if audit.exists() else "# Agent log\n"
                if marker not in content:
                    audit.write_text(
                        f"{content}\n{marker}\n## {timestamp()} — {record.ticket}\n\n"
                        f"- Merged after passing project gates; {record.attempts} attempts, "
                        f"effort {record.effort}.\n",
                        encoding="utf-8",
                    )
                self.manager.commit_all(path, f"Record {record.ticket} validation")
                record = self._change(record, Phase.SYNCING_LOG)
            elif phase == Phase.CLEANUP:
                self.manager.remove(record.ticket)
                return self._change(record, Phase.DONE, finished_at=timestamp())
            else:
                raise LaneError(f"Cannot finish ticket from {record.phase}")
