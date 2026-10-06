"""Exercise lanes with temporary git repositories and real offline companion jobs."""

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.conftest import FakeCompanion
from tests.integration.test_worktree import git
from tests.integration.test_worktree import manager as manager

from cc_tandem import backlog, companion
from cc_tandem.backlog import Status, Ticket
from cc_tandem.companion import CompanionResult, JsonValue
from cc_tandem.config import CodexConfig, Config, LanesConfig, PromptConfig, QueueEntry
from cc_tandem.effort import Effort
from cc_tandem.lanes import LaneError, Runner, build_prompt, inside
from cc_tandem.state import Phase, StateStore, TicketState, timestamp
from cc_tandem.worktree import WorktreeError, WorktreeManager

pytestmark = pytest.mark.integration
RULES = "**Rules for every ticket.** Work only in your worktree. Pass the gate.\n\n---\n\n"
GATE = (
    sys.executable,
    "-c",
    "from pathlib import Path; assert Path('gate.txt').read_text() == 'ok'",
)


@dataclass
class Jobs:
    fake: FakeCompanion
    specs: dict[str, list[dict[str, JsonValue] | str]] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_run = companion.run

        def routed(args: Sequence[str]) -> CompanionResult:
            self.calls.append(tuple(args))
            if args[0] == "task":
                path = Path(args[args.index("--cwd") + 1])
                ticket = path.name
                count = self.counts.get(ticket, 0)
                self.counts[ticket] = count + 1
                sequence = self.specs.get(ticket, ["succeed"])
                spec = sequence[min(count, len(sequence) - 1)]
                config: dict[str, JsonValue] = (
                    {"scenario": spec} if isinstance(spec, str) else spec.copy()
                )
                status = Status(str(config.pop("ticket_status", Status.DONE)))
                if config.get("scenario", "succeed") == "succeed":
                    tickets = [
                        replace(item, status=status) if item.id == ticket else item
                        for item in backlog.read(path / "BACKLOG.md")
                    ]
                    files = config.get("files", {})
                    assert isinstance(files, dict)
                    config["files"] = {
                        **files,
                        "BACKLOG.md": RULES
                        + "\n---\n\n".join(backlog.render(item) for item in tickets),
                        f"{ticket}.txt": "implemented\n",
                    }
                self.fake.scenario_file.write_text(json.dumps(config))
            return real_run(args)

        monkeypatch.setattr(companion, "run", routed)

    @property
    def efforts(self) -> list[str]:
        return [args[args.index("--effort") + 1] for args in self.calls if args[0] == "task"]


@pytest.fixture
def repository(manager: WorktreeManager) -> WorktreeManager:
    tickets = [
        Ticket(
            f"T-{number:02}", f"Example {number}", Effort.LOW, "Implement the example.", Status.TODO
        )
        for number in range(1, 5)
    ]
    (manager.repo / "BACKLOG.md").write_text(
        RULES + "\n---\n\n".join(backlog.render(item) for item in tickets)
    )
    (manager.repo / "AGENTS.md").write_text("Project instructions\n")
    (manager.repo / "gate.txt").write_text("ok")
    (manager.repo / ".gitignore").write_text(".tandem/\n")
    (manager.repo / ".gitattributes").write_text(
        "BACKLOG.md merge=union\ndocs/AGENT-LOG.md merge=union\n"
    )
    from cc_tandem.worktree import commit_all

    commit_all(manager.repo, "Add test project instructions")
    return manager


@pytest.fixture
def jobs(offline_companion: FakeCompanion, monkeypatch: pytest.MonkeyPatch) -> Jobs:
    jobs = Jobs(offline_companion)
    jobs.install(monkeypatch)
    return jobs


def runner(
    manager: WorktreeManager,
    *,
    count: int = 1,
    tickets: tuple[str, ...] = ("T-01",),
    store: StateStore | None = None,
    max_attempts: int = 3,
) -> Runner:
    config = Config(
        CodexConfig(),
        LanesConfig(count=count),
        PromptConfig(("AGENTS.md",)),
        tuple(QueueEntry(ticket, Effort.LOW) for ticket in tickets),
    )
    return Runner(
        manager.repo,
        config,
        manager=manager,
        store=store,
        checks=(GATE,),
        sleep=lambda _: None,
        poll_seconds=0.01,
        backoff_seconds=1,
        max_attempts=max_attempts,
    )


def test_happy_path_across_ordered_lanes(repository: WorktreeManager, jobs: Jobs) -> None:
    run = runner(repository, count=2, tickets=("T-01", "T-02", "T-03", "T-04"))
    result = run.run()
    assert all(item.phase == Phase.DONE and item.attempts == 1 for item in result.values())
    assert {item.lane for item in result.values()} == {0, 1}
    for ticket in result:
        assert (repository.repo / f"{ticket}.txt").read_text() == "implemented\n"
        assert not repository.path_for(ticket).exists()
        assert git(repository.repo, "rev-parse", ticket)
        assert Path(result[ticket].logs[0]).is_file()
    calls = [Path(args[args.index("--cwd") + 1]).name for args in jobs.calls if args[0] == "task"]
    assert calls.index("T-01") < calls.index("T-03")
    assert calls.index("T-02") < calls.index("T-04")
    assert git(repository.repo, "status", "--porcelain") == ""
    audit = (repository.repo / "docs/AGENT-LOG.md").read_text()
    assert all(f"tandem:{ticket}:" in audit for ticket in result)
    tasks_before = len(jobs.efforts)
    assert run.run() == result
    assert len(jobs.efforts) == tasks_before


def test_capacity_preserves_budget_and_effort(repository: WorktreeManager, jobs: Jobs) -> None:
    jobs.specs["T-01"] = ["usage_limit", "succeed"]
    saved = runner(repository).run()["T-01"]
    assert saved.phase == Phase.DONE
    assert saved.attempts == 1
    assert saved.backoff_count == 1
    assert jobs.efforts == ["low", "low"]
    assert len(saved.logs) == 2


def test_repeated_real_failures_escalate_once(repository: WorktreeManager, jobs: Jobs) -> None:
    jobs.specs["T-01"] = ["fail", "fail", "succeed"]
    saved = runner(repository).run()["T-01"]
    assert saved.phase == Phase.DONE and saved.attempts == 3
    assert jobs.efforts == ["low", "medium", "high"]


def test_implementation_failure_needs_repeat_before_escalation(
    repository: WorktreeManager, jobs: Jobs
) -> None:
    bad: dict[str, JsonValue] = {"scenario": "succeed", "files": {"gate.txt": "bad"}}
    jobs.specs["T-01"] = [bad, bad, {"scenario": "succeed", "files": {"gate.txt": "ok"}}]
    saved = runner(repository).run()["T-01"]
    assert saved.phase == Phase.DONE and saved.attempts == 3
    assert jobs.efforts == ["low", "low", "medium"]
    assert saved.implementation_failures == 1
    assert json.loads(Path(saved.logs[1]).read_text())["outcome"] == "stalled"


def test_exhaustion_stops_lane(repository: WorktreeManager, jobs: Jobs) -> None:
    jobs.specs["T-01"] = ["fail"]
    run = runner(repository, tickets=("T-01", "T-02"), max_attempts=2)
    result = run.run()
    assert result["T-01"].phase == Phase.EXHAUSTED
    assert result["T-01"].attempts == 2
    assert result["T-02"].phase == Phase.PENDING
    assert repository.path_for("T-01").exists()
    run.run()
    assert jobs.counts == {"T-01": 2}


def test_blocked_lane_does_not_stop_other_lane(repository: WorktreeManager, jobs: Jobs) -> None:
    jobs.specs["T-01"] = [{"ticket_status": Status.BLOCKED}]
    result = runner(repository, count=2, tickets=("T-01", "T-02", "T-03")).run()
    assert result["T-01"].phase == Phase.BLOCKED
    assert result["T-02"].phase == Phase.DONE
    assert result["T-03"].phase == Phase.PENDING
    assert repository.path_for("T-01").exists()
    assert (
        backlog.get_ticket(backlog.read(repository.path_for("T-01") / "BACKLOG.md"), "T-01").status
        == Status.BLOCKED
    )


def test_real_merge_conflict_stops_lane(repository: WorktreeManager, jobs: Jobs) -> None:
    path = repository.create("T-01")
    (repository.repo / "shared.txt").write_text("base change\n")
    from cc_tandem.worktree import commit_all

    commit_all(repository.repo, "Advance base")
    jobs.specs["T-01"] = [{"files": {"shared.txt": "lane change\n"}}]
    result = runner(repository, count=2, tickets=("T-01", "T-02", "T-03")).run()
    assert result["T-01"].phase == Phase.CONFLICT
    assert "shared.txt" in (result["T-01"].error or "")
    assert result["T-02"].phase == Phase.DONE
    assert result["T-03"].phase == Phase.PENDING
    assert path.is_dir()
    assert git(path, "status", "--porcelain") == ""


class Crash(BaseException):
    """Simulate process death without the runner's ordinary failure handler."""


@pytest.mark.parametrize(
    "phase",
    [
        Phase.RUNNING,
        Phase.COMMITTING,
        Phase.SYNCING,
        Phase.MERGING,
        Phase.LOGGING,
        Phase.SYNCING_LOG,
        Phase.MERGING_LOG,
        Phase.CLEANUP,
    ],
)
def test_resume_at_every_publication_checkpoint(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch, phase: Phase
) -> None:
    run = runner(repository)
    real_put = run.store.put
    crashed = False

    def put(record: TicketState) -> TicketState:
        nonlocal crashed
        saved = real_put(record)
        if saved.phase == phase and not crashed:
            crashed = True
            raise Crash()
        return saved

    monkeypatch.setattr(run.store, "put", put)
    with pytest.raises(Crash):
        run.run()
    assert run.store.read()["T-01"].phase == phase
    # Deliberately under-report aggregate jobs: per-job status must still be used.
    jobs.fake.scenario_file.write_text('{"status": {"running": [], "recent": []}}')
    result = runner(repository).run()["T-01"]
    assert result.phase == Phase.DONE
    assert jobs.counts == {"T-01": 1}
    assert (repository.repo / "docs/AGENT-LOG.md").read_text().count("<!-- tandem:T-01:") == 1


def test_resume_after_remove_before_checkpoint(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_remove = repository.remove

    def remove(ticket: str) -> None:
        real_remove(ticket)
        raise Crash()

    monkeypatch.setattr(repository, "remove", remove)
    with pytest.raises(Crash):
        runner(repository).run()
    monkeypatch.setattr(repository, "remove", real_remove)
    assert runner(repository).run()["T-01"].phase == Phase.DONE
    assert jobs.counts == {"T-01": 1}


def test_already_done_is_skipped(repository: WorktreeManager, jobs: Jobs) -> None:
    backlog.set_status(
        repository.repo / "BACKLOG.md",
        "T-01",
        Status.DONE,
        "Existing completion",
        expected_status=Status.TODO,
    )
    result = runner(repository).run()["T-01"]
    assert result.phase == Phase.SKIPPED and result.attempts == 0
    assert jobs.counts == {}
    assert not repository.path_for("T-01").exists()


def test_prompt_uses_config_ticket_and_rules(repository: WorktreeManager, jobs: Jobs) -> None:
    runner(repository).run()
    task = next(args for args in jobs.calls if args[0] == "task")
    assert "AGENTS.md" in task[-1] and "ticket T-01" in task[-1]
    assert RULES.split("\n")[0] in task[-1]
    assert "Run no git commands" in task[-1]


@pytest.mark.parametrize("name", ["../escape", "/outside", "link/escape"])
def test_containment(tmp_path: Path, name: str) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(LaneError, match="escapes"):
        inside(root, name)
    assert inside(root, "safe") == root / "safe"


def test_empty_read_first_prompt() -> None:
    ticket = Ticket("T-01", "Example", Effort.LOW, "", Status.TODO)
    assert "(none)" in build_prompt(ticket, (), "rules")


def checkpoint(run: Runner, phase: Phase, *, create: bool = True) -> TicketState:
    path = run.manager.create("T-01") if create else None
    now = timestamp()
    return run.store.put(
        TicketState(
            "T-01",
            0,
            Effort.LOW,
            now,
            now,
            phase=phase,
            worktree=str(path) if path else None,
        )
    )


def test_background_submission_and_diagnostic_log(repository: WorktreeManager, jobs: Jobs) -> None:
    saved = runner(repository).run()["T-01"]
    task = next(args for args in jobs.calls if args[0] == "task")
    assert {"--background", "--write", "--fresh"}.issubset(task)
    log = json.loads(Path(saved.logs[0]).read_text())
    assert log["outcome"] == "ok" and log["job_id"] == saved.job_id
    assert log["effort"] == "low" and log["checks"][0]["returncode"] == 0


def test_queue_invariants(repository: WorktreeManager, jobs: Jobs) -> None:
    run = runner(repository)
    entry = QueueEntry("T-01", Effort.LOW)
    with pytest.raises(LaneError, match="more than one lane"):
        run.run([[entry], [entry]])
    checkpoint(run, Phase.PENDING, create=False)
    with pytest.raises(LaneError, match="different lane"):
        run.run([[], [entry]])
    assert jobs.counts == {}


def test_empty_queue(repository: WorktreeManager, jobs: Jobs) -> None:
    assert runner(repository, tickets=()).run([]) == {}
    assert jobs.counts == {}


@pytest.mark.parametrize("status", [Status.BLOCKED, Status.UNREFINED])
def test_unrunnable_base_ticket(repository: WorktreeManager, jobs: Jobs, status: Status) -> None:
    backlog.set_status(
        repository.repo / "BACKLOG.md", "T-01", status, "Needs input", expected_status=Status.TODO
    )
    saved = runner(repository).run()["T-01"]
    assert saved.phase == (Phase.BLOCKED if status == Status.BLOCKED else Phase.FAILED)
    if status == Status.UNREFINED:
        assert "UNREFINED" in (saved.error or "")
    assert saved.finished_at and not repository.path_for("T-01").exists()
    assert jobs.counts == {}


@pytest.mark.parametrize("phase", [Phase.DONE, Phase.SKIPPED])
def test_completed_checkpoint_disagrees_with_backlog(
    repository: WorktreeManager, jobs: Jobs, phase: Phase
) -> None:
    run = runner(repository)
    checkpoint(run, phase, create=False)
    saved = run.run()["T-01"]
    assert saved.phase == Phase.FAILED and "disagrees" in (saved.error or "")
    assert jobs.counts == {}


def test_stale_worktree_removed_for_done_base(repository: WorktreeManager, jobs: Jobs) -> None:
    run = runner(repository)
    checkpoint(run, Phase.PREPARING)
    backlog.set_status(
        repository.repo / "BACKLOG.md",
        "T-01",
        Status.DONE,
        "Completed",
        expected_status=Status.TODO,
    )
    saved = run.run()["T-01"]
    assert saved.phase == Phase.SKIPPED and not repository.path_for("T-01").exists()
    assert jobs.counts == {}


@pytest.mark.parametrize("phase", [Phase.SUBMITTING, Phase.WAITING])
def test_incomplete_checkpoint_is_not_relaunched(
    repository: WorktreeManager, jobs: Jobs, phase: Phase
) -> None:
    run = runner(repository)
    checkpoint(run, phase)
    saved = run.run()["T-01"]
    assert saved.phase == Phase.FAILED
    assert ("without job ID" if phase == Phase.SUBMITTING else "no retry deadline") in (
        saved.error or ""
    )
    assert jobs.counts == {}


def test_blocked_worktree_on_resume(repository: WorktreeManager, jobs: Jobs) -> None:
    run = runner(repository)
    saved = checkpoint(run, Phase.PREPARING)
    assert saved.worktree
    backlog.set_status(
        Path(saved.worktree) / "BACKLOG.md",
        "T-01",
        Status.BLOCKED,
        "Options",
        expected_status=Status.TODO,
    )
    assert run.run()["T-01"].phase == Phase.BLOCKED
    assert jobs.counts == {}


def test_expired_wait_retries_without_sleep(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runner(repository)
    saved = checkpoint(run, Phase.WAITING)
    run.store.put(replace(saved, retry_at=(datetime.now(UTC) - timedelta(days=1)).isoformat()))
    sleep = Mock()
    monkeypatch.setattr(run, "sleep", sleep)
    assert run.run()["T-01"].phase == Phase.DONE
    sleep.assert_not_called()


@pytest.mark.parametrize("outcome", ["pass", "fail", "retry"])
def test_recover_done_worktree_before_submission(
    repository: WorktreeManager, jobs: Jobs, outcome: str
) -> None:
    run = runner(repository, max_attempts=1 if outcome == "fail" else 3)
    saved = checkpoint(run, Phase.PREPARING)
    assert saved.worktree
    path = Path(saved.worktree)
    backlog.set_status(
        path / "BACKLOG.md", "T-01", Status.DONE, "Recovered", expected_status=Status.TODO
    )
    if outcome != "pass":
        (path / "gate.txt").write_text("bad")
    if outcome == "retry":
        jobs.specs["T-01"] = [{"files": {"gate.txt": "ok"}}]
    result = run.run()["T-01"]
    assert result.phase == (Phase.EXHAUSTED if outcome == "fail" else Phase.DONE)
    assert jobs.counts == ({"T-01": 1} if outcome == "retry" else {})


@pytest.mark.parametrize("kind", ["dry", "no_id", "blocked", "done"])
def test_submission_without_job_id(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    run = runner(repository, max_attempts=1)

    def launch(args: Sequence[str]) -> CompanionResult:
        assert args[0] == "task"
        path = Path(args[args.index("--cwd") + 1])
        if kind in {"blocked", "done"}:
            backlog.set_status(
                path / "BACKLOG.md",
                "T-01",
                Status.BLOCKED if kind == "blocked" else Status.DONE,
                "Result",
                expected_status=Status.TODO,
            )
        return CompanionResult(0, "", "", dry_run=kind == "dry", json={})

    monkeypatch.setattr(run, "_call", launch)
    saved = run.run()["T-01"]
    expected = {
        "dry": Phase.FAILED,
        "no_id": Phase.EXHAUSTED,
        "blocked": Phase.BLOCKED,
        "done": Phase.DONE,
    }
    assert saved.phase == expected[kind]
    if kind == "dry":
        assert "Dry run" in (saved.error or "")
    assert jobs.counts == {}


def test_sync_gate_failure_retains_clean_branch(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runner(repository)
    sync = repository.sync_base_into

    def advance(ticket: str) -> object:
        from cc_tandem.worktree import commit_all

        (repository.repo / "gate.txt").write_text("bad")
        commit_all(repository.repo, "Advance gate")
        return sync(ticket)

    monkeypatch.setattr(repository, "sync_base_into", advance)
    saved = run.run()["T-01"]
    assert saved.phase == Phase.FAILED and "after syncing base" in (saved.error or "")
    assert repository.path_for("T-01").is_dir()
    assert git(repository.path_for("T-01"), "status", "--porcelain") == ""


def test_failed_base_merge_aborts_and_retains_lane(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = repository.merge_into_base
    jobs.specs["T-01"] = [{"files": {"shared.txt": "lane edit\n"}}]

    def advance(ticket: str) -> object:
        from cc_tandem.worktree import commit_all

        (repository.repo / "shared.txt").write_text("base edit\n")
        commit_all(repository.repo, "Advance before merge")
        return merge(ticket)

    monkeypatch.setattr(repository, "merge_into_base", advance)
    saved = runner(repository).run()["T-01"]
    assert saved.phase == Phase.CONFLICT and "shared.txt" in (saved.error or "")
    assert repository.path_for("T-01").is_dir()
    assert git(repository.repo, "status", "--porcelain") == ""
    assert not (repository.repo / ".git/MERGE_HEAD").exists()
    assert "<!-- tandem:T-01:" not in (
        (repository.repo / "docs/AGENT-LOG.md").read_text()
        if (repository.repo / "docs/AGENT-LOG.md").exists()
        else ""
    )


def test_logging_recovery_does_not_duplicate_audit(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runner(repository)
    commit = repository.commit_all

    def crash_after_audit(path: Path, message: str) -> bool:
        if message.startswith("Record"):
            raise Crash()
        return commit(path, message)

    monkeypatch.setattr(repository, "commit_all", crash_after_audit)
    with pytest.raises(Crash):
        run.run()
    monkeypatch.setattr(repository, "commit_all", commit)
    assert run.run()["T-01"].phase == Phase.DONE
    assert (repository.repo / "docs/AGENT-LOG.md").read_text().count("<!-- tandem:T-01:") == 1
    assert jobs.counts == {"T-01": 1}


def test_done_base_reconciles_running_job_before_cleanup(
    repository: WorktreeManager, jobs: Jobs
) -> None:
    jobs.specs["T-01"] = ["no_change"]
    run = runner(repository)
    saved = checkpoint(run, Phase.RUNNING)
    assert saved.worktree
    launched = run._call(["task", "--background", "--cwd", saved.worktree, "implement"])
    assert isinstance(launched.json, dict)
    job_id = launched.json["jobId"]
    assert isinstance(job_id, str)
    run.store.put(replace(saved, job_id=job_id))
    backlog.set_status(
        repository.repo / "BACKLOG.md",
        "T-01",
        Status.DONE,
        "Completed elsewhere",
        expected_status=Status.TODO,
    )
    result = run.run()["T-01"]
    assert result.phase == Phase.SKIPPED and not repository.path_for("T-01").exists()
    assert ("status", job_id, "--json") in jobs.calls
    assert ("result", job_id, "--json") in jobs.calls
    assert jobs.counts == {"T-01": 1}


def test_launch_capacity_error_waits_and_retries_same_effort(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runner(repository)
    call = run._call
    failed = False
    efforts: list[Effort] = []

    def limited(args: Sequence[str]) -> CompanionResult:
        nonlocal failed
        if args[0] == "task":
            efforts.append(Effort.parse(args[args.index("--effort") + 1]))
            if not failed:
                failed = True
                return CompanionResult(1, "Usage limit reached", "")
        return call(args)

    sleep = Mock()
    monkeypatch.setattr(run, "_call", limited)
    monkeypatch.setattr(run, "sleep", sleep)
    saved = run.run()["T-01"]
    assert saved.phase == Phase.DONE and saved.attempts == 1 and saved.backoff_count == 1
    assert efforts == [Effort.LOW, Effort.LOW]
    sleep.assert_called_once()
    assert 0 < sleep.call_args.args[0] <= 1


def test_second_submission_never_launches_into_owned_worktree(
    repository: WorktreeManager, jobs: Jobs
) -> None:
    run = runner(repository)
    path = repository.create("T-01")
    with repository.ownership("T-01").acquire("existing-job"):
        saved = run.run()["T-01"]
        assert saved.phase == Phase.FAILED
        assert "existing-job" in (saved.error or "")
        assert jobs.calls == []
        assert path.exists()


def test_ownership_is_published_before_poll_and_held_for_job(
    repository: WorktreeManager, jobs: Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runner(repository)
    call = run._call
    polled: list[str] = []

    def inspect(args: Sequence[str]) -> CompanionResult:
        lock = repository.ownership("T-01")
        if args[0] == "task":
            assert json.loads(lock.path.read_text())["job_id"].startswith("submitting-")
        elif args[0] == "status":
            polled.append(args[1])
            assert json.loads(lock.path.read_text())["job_id"] == args[1]
            with pytest.raises(WorktreeError, match=args[1]):
                repository.remove("T-01")
            with pytest.raises(WorktreeError, match=args[1]), lock.acquire():
                pytest.fail("A second job cannot start while polling")
        return call(args)

    monkeypatch.setattr(run, "_call", inspect)
    assert run.run()["T-01"].phase == Phase.DONE
    assert polled
    assert not repository.ownership("T-01").path.exists()
