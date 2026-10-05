"""Exercise lanes with temporary git repositories and real offline companion jobs."""

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

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
from cc_tandem.state import Phase, StateStore, TicketState
from cc_tandem.worktree import WorktreeManager

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
