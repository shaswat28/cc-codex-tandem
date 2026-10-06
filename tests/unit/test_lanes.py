"""Lane contracts and companion protocol errors without worker execution."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from cc_tandem.companion import CompanionResult
from cc_tandem.config import CodexConfig, Config, LanesConfig, PromptConfig
from cc_tandem.effort import Effort
from cc_tandem.lanes import LaneError, Runner, _git_output, _rules
from cc_tandem.state import Phase, StateStore, TicketState
from cc_tandem.worktree import WorktreeManager
from tests.conftest import FakeCompanion


def make_runner(tmp_path: Path) -> Runner:
    manager = Mock(spec=WorktreeManager, repo=tmp_path)
    return Runner(
        tmp_path,
        Config(CodexConfig(), LanesConfig(), PromptConfig(), ()),
        manager=manager,
        sleep=Mock(),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**Rules for every ticket.** Read.\n## T-01", "**Rules for every ticket.** Read."),
        ("**Rules for every ticket.** Read.\n---", "**Rules for every ticket.** Read."),
        ("**Rules for every ticket.** Read.\n", "**Rules for every ticket.** Read."),
    ],
)
def test_rules_boundaries(text: str, expected: str) -> None:
    assert _rules(text) == expected


def test_missing_rules() -> None:
    with pytest.raises(LaneError, match="no Rules"):
        _rules("# Backlog")


def test_git_inspection_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cc_tandem.lanes.subprocess.run", Mock(return_value=Mock(returncode=1, stderr="broken"))
    )
    with pytest.raises(LaneError, match="broken"):
        _git_output(tmp_path, "status")


@pytest.mark.parametrize("kind", ["isolation", "checks", "poll", "manager", "store"])
def test_constructor_rejects_invalid_contracts(tmp_path: Path, kind: str) -> None:
    config = Config(
        CodexConfig(),
        LanesConfig(isolation="none" if kind == "isolation" else "worktree"),
        PromptConfig(),
        (),
    )
    manager = Mock(spec=WorktreeManager, repo=tmp_path / "other" if kind == "manager" else tmp_path)
    store = StateStore(tmp_path / "other" if kind == "store" else tmp_path)
    with pytest.raises((LaneError, ValueError)):
        Runner(
            tmp_path,
            config,
            manager=manager,
            store=store,
            checks=() if kind == "checks" else ("gate",),
            poll_seconds=0 if kind == "poll" else 1,
        )


@pytest.mark.parametrize(
    "kind", ["absent", "wrong", "coordinator", "missing", "symlink", "artifact"]
)
def test_checkpoint_checkout_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    run = make_runner(tmp_path)
    expected = tmp_path / "lane"
    expected.mkdir()
    if kind == "missing":
        expected.rmdir()
    elif kind == "symlink":
        expected.rmdir()
        expected.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "artifact":
        (expected / "escape").symlink_to(tmp_path / "outside")
    if kind == "coordinator":
        expected = tmp_path
    monkeypatch.setattr(run.manager, "path_for", Mock(return_value=expected))
    monkeypatch.setattr("cc_tandem.lanes._git_output", Mock(return_value="escape\0"))
    record = TicketState(
        "T-01",
        0,
        Effort.LOW,
        "now",
        "now",
        worktree=None
        if kind == "absent"
        else str(tmp_path / "wrong")
        if kind == "wrong"
        else str(expected),
    )
    with pytest.raises(LaneError, match=r"worktree|symlink"):
        run._checkout(record)


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([CompanionResult(1, "", "missing")], "Cannot reconcile"),
        ([CompanionResult(0, "", "", json=[])], "Cannot reconcile"),
        ([CompanionResult(0, "", "", json={"job": {"status": "mystery"}})], "Unknown job"),
        (
            [
                CompanionResult(0, "", "", json={"job": {"status": "completed"}}),
                CompanionResult(1, "", "unavailable"),
            ],
            "Cannot retrieve",
        ),
    ],
)
def test_job_protocol_errors(tmp_path: Path, results: list[CompanionResult], message: str) -> None:
    run = make_runner(tmp_path)
    run._call = Mock(side_effect=results)  # type: ignore[method-assign]
    record = TicketState("T-01", 0, Effort.LOW, "now", "now", job_id="job")
    with pytest.raises(LaneError, match=message):
        run._await_job(record)


def test_job_requires_id(tmp_path: Path) -> None:
    run = make_runner(tmp_path)
    with pytest.raises(LaneError, match="no job ID"):
        run._await_job(TicketState("T-01", 0, Effort.LOW, "now", "now"))


def test_active_job_is_polled_and_failed_terminal_is_preserved(tmp_path: Path) -> None:
    run = make_runner(tmp_path)
    call = Mock(
        side_effect=[
            CompanionResult(0, "", "", json={"job": {"status": "running"}}),
            CompanionResult(0, "", "", json={"job": {"status": "failed", "summary": "failure"}}),
            CompanionResult(0, "diagnostics", ""),
        ]
    )
    run._call = call  # type: ignore[method-assign]
    record = TicketState("T-01", 0, Effort.LOW, "now", "now", job_id="job")
    result = run._await_job(record)
    assert result.exit_code == 1 and result.stdout == "diagnostics\nfailure"
    assert call.call_count == 3
    assert isinstance(run.sleep, Mock)
    run.sleep.assert_called_once_with(run.poll_seconds)


def test_publish_rejects_nonfinishing_phase(tmp_path: Path) -> None:
    run = make_runner(tmp_path)
    run._checkout = Mock(return_value=tmp_path)  # type: ignore[method-assign]
    with pytest.raises(LaneError, match="Cannot finish"):
        run._publish(TicketState("T-01", 0, Effort.LOW, "now", "now", phase=Phase.PENDING))


@pytest.mark.parametrize("reported", ["missing", "completed"])
def test_live_worker_outweighs_missing_or_terminal_companion_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reported: str
) -> None:
    run = make_runner(tmp_path)
    early = (
        CompanionResult(1, "No job found", "")
        if reported == "missing"
        else CompanionResult(0, "", "", json={"job": {"status": "completed"}})
    )
    run._call = Mock(  # type: ignore[method-assign]
        side_effect=[
            early,
            CompanionResult(0, "", "", json={"job": {"status": "completed"}}),
            CompanionResult(0, "finished", ""),
        ]
    )
    monkeypatch.setattr("cc_tandem.lanes.worker_alive", Mock(side_effect=[True, False]))
    record = TicketState("T-01", 0, Effort.LOW, "now", "now", job_id="job")
    assert run._await_job(record).stdout == "finished\n"
    assert isinstance(run.sleep, Mock)
    run.sleep.assert_called_once_with(run.poll_seconds)


def test_question_stops_lane_and_is_reported_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline_companion: FakeCompanion
) -> None:
    import json

    from cc_tandem import companion
    from cc_tandem.backlog import Status, Ticket
    from cc_tandem.lanes import build_prompt

    question = "Setup is unavailable. Should I retry or stop?\n1. Retry\n2. Stop\n"
    offline_companion.configure({"scenario": "awaiting_input", "question": question})
    run = make_runner(tmp_path)
    ticket = Ticket("T-01", "Example", Effort.LOW, "Implement.", Status.TODO)
    monkeypatch.setattr(run, "_ticket", Mock(return_value=ticket))
    monkeypatch.setattr(run, "_checkout", Mock(return_value=tmp_path))
    monkeypatch.setattr("cc_tandem.lanes._diff", Mock(return_value=False))
    record = run.store.put(
        TicketState("T-01", 0, Effort.LOW, "2026-10-05T00:00:00+00:00", "2026-10-05T00:00:00+00:00")
    )
    launched = companion.run(["task", "--background", "Example"])
    assert isinstance(launched.json, dict)
    job_id = launched.json["jobId"]
    assert isinstance(job_id, str)
    from dataclasses import replace

    record = run.store.put(replace(record, phase=Phase.RUNNING, job_id=job_id))
    result = run._await_job(record)
    stopped = run._evaluate(record, result)
    assert stopped.phase == Phase.BLOCKED
    assert stopped.effort == Effort.LOW and stopped.attempts == 0
    assert stopped.error == question and stopped.retry_at is None
    assert stopped.finished_at is not None
    report = json.loads(Path(stopped.logs[0]).read_text())
    assert report["outcome"] == "awaiting_input" and report["question"] == question
    assert run._run_ticket(stopped) == stopped
    assert json.loads(offline_companion.state_file.read_text())["attempt"] == 1
    assert isinstance(run.manager.remove, Mock)
    run.manager.remove.assert_not_called()
    prompt = build_prompt(ticket, (), "Rules")
    assert "product decision" in prompt and "environmental or operational" in prompt
    assert "Never end by asking a question" in prompt
