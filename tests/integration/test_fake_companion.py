"""Exercise the real Python/Node boundary, exclusively against the offline fake."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from tests.conftest import FakeCompanion

from cc_tandem import companion
from cc_tandem.backlog import Status
from cc_tandem.effort import FailureClass

pytestmark = pytest.mark.integration

TASK = ["task", "--write", "--fresh", "--model", "test-model", "--effort", "medium", "Fix it"]


@pytest.mark.parametrize(
    ("scenario", "outcome", "code"),
    [
        ("succeed", "ok", 0),
        ("fail", "stalled", 1),
        ("usage_limit", "capacity", 1),
        ("exit_nonzero", "infrastructure", 7),
        ("invalid_json", "stalled", 0),
        ("no_change", "stalled", 0),
    ],
)
def test_scenario_classification(
    fake_companion: FakeCompanion, scenario: str, outcome: FailureClass, code: int
) -> None:
    fake_companion.configure(scenario)
    result = companion.run(TASK)
    assert result.exit_code == code
    assert (
        companion.classify(
            result,
            turn_started=scenario != "exit_nonzero",
            ticket_status=Status.DONE if scenario == "succeed" else None,
        )
        == outcome
    )
    assert result.command == ("node", str(fake_companion.script), *TASK)
    if scenario == "invalid_json":
        assert result.json is None
        assert result.stdout == "{invalid JSON\n"
        with pytest.raises(companion.CompanionInvalid, match="JSON object"):
            companion.status_all()
    elif scenario == "exit_nonzero":
        assert "Fake process error" in result.stderr
        with pytest.raises(companion.CompanionInvalid, match=r"failed \(7\)"):
            companion.status_all()
    else:
        assert isinstance(result.json, dict)


def test_hang_until_terminated(
    fake_companion: FakeCompanion, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_companion.configure("hang")

    def bounded_run(
        args: Sequence[str], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check
        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as job:
            try:
                job.communicate(timeout=0.5)
                pytest.fail("Hang scenario returned before cancellation")
            except subprocess.TimeoutExpired:
                job.terminate()
                stdout, stderr = job.communicate(timeout=5)
            assert job.returncode is not None
            return subprocess.CompletedProcess(args, job.returncode, stdout, stderr)

    monkeypatch.setattr("cc_tandem.companion.subprocess.run", bounded_run)
    result = companion.run(TASK)
    assert result.exit_code < 0
    assert result.stdout == "Fake task running\n"
    assert companion.classify(result) == "stalled"


def test_success_edits_only_working_directory(fake_companion: FakeCompanion) -> None:
    fake_companion.configure(
        {"scenario": "succeed", "files": {"nested/output.txt": "real content\n"}}
    )
    assert companion.classify(companion.run(TASK), ticket_status=Status.DONE) == "ok"
    assert Path("nested/output.txt").read_text() == "real content\n"


def test_no_change_ignores_edits(fake_companion: FakeCompanion) -> None:
    Path("existing.txt").write_text("original")
    fake_companion.configure(
        {"scenario": "no_change", "files": {"existing.txt": "replacement", "new.txt": "new"}}
    )
    assert companion.classify(companion.run(TASK)) == "stalled"
    assert Path("existing.txt").read_text() == "original"
    assert not Path("new.txt").exists()


def test_cwd_argument(fake_companion: FakeCompanion, tmp_path: Path) -> None:
    target = tmp_path / "other workspace"
    target.mkdir()
    fake_companion.configure({"files": {"output.txt": "target"}})
    assert (
        companion.classify(companion.run([*TASK, "--cwd", str(target)]), ticket_status=Status.DONE)
        == "ok"
    )
    assert (target / "output.txt").read_text() == "target"
    assert not Path("output.txt").exists()


@pytest.mark.parametrize("escape", ["../escape.txt", "absolute", "symlink", "file_symlink"])
def test_edit_escape_rejected(fake_companion: FakeCompanion, tmp_path: Path, escape: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("original")
    name = escape
    if escape == "absolute":
        name = str(protected)
    elif escape == "symlink":
        Path("link").symlink_to(outside, target_is_directory=True)
        name = "link/protected.txt"
    elif escape == "file_symlink":
        Path("link.txt").symlink_to(protected)
        name = "link.txt"
    fake_companion.configure({"files": {"valid.txt": "also rejected", name: "changed"}})
    result = companion.run(TASK)
    assert companion.classify(result) == "stalled"
    assert "escapes working directory" in result.stderr
    assert protected.read_text() == "original"
    assert not (tmp_path / "escape.txt").exists()
    assert not Path("valid.txt").exists()
    assert companion.status_all().recent[0].status == "failed"


def test_status_shape_and_custom_records(fake_companion: FakeCompanion) -> None:
    assert companion.status_all() == companion.CompanionStatus([], [])
    fake_companion.configure(
        {
            "status": {
                "running": [{"id": "live-1", "status": "running", "extra": True}],
                "recent": [{"id": "done-1", "status": "completed", "summary": "Finished"}],
            }
        }
    )
    assert companion.status_all() == companion.CompanionStatus(
        [companion.StatusRecord("live-1", "running")],
        [companion.StatusRecord("done-1", "completed", "Finished")],
    )


def test_retry_sequence(fake_companion: FakeCompanion) -> None:
    fake_companion.configure(
        {"attempts": ["usage_limit", "fail", {"scenario": "succeed", "files": {"done": "yes"}}]}
    )
    for expected in ("capacity", "stalled", "ok", "ok"):
        assert (
            companion.classify(
                companion.run(TASK), ticket_status=Status.DONE if expected == "ok" else None
            )
            == expected
        )
    assert Path("done").read_text() == "yes"
    assert len(companion.status_all().recent) == 4


def test_background_status_result_cancel(fake_companion: FakeCompanion) -> None:
    fake_companion.configure("hang")
    started = companion.run([*TASK, "--background"])
    assert isinstance(started.json, dict)
    assert started.json["jobId"] == "fake-job-1"
    assert started.json["status"] == "queued"
    assert companion.status_all().running == [
        companion.StatusRecord("fake-job-1", "running", "Fake hang")
    ]
    status = companion.run(["status", "fake-job-1", "--json"])
    assert isinstance(status.json, dict)
    assert isinstance(status.json["job"], dict)
    assert status.json["job"]["status"] == "running"
    result = companion.run(["result", "fake-job-1", "--json"])
    assert isinstance(result.json, dict)
    assert result.json["storedJob"] == status.json["job"]
    cancelled = companion.run(["cancel", "fake-job-1", "--json"])
    assert cancelled.json == {"jobId": "fake-job-1", "status": "cancelled"}
    assert companion.status_all().running == []
    assert companion.status_all().recent[0].status == "cancelled"


def test_environment_scenario(
    fake_companion: FakeCompanion, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CC_TANDEM_FAKE_SCENARIO_FILE")
    monkeypatch.setenv("CC_TANDEM_FAKE_SCENARIO", '"usage_limit"')
    assert companion.classify(companion.run(TASK)) == "capacity"


def test_unknown_scenario_fails_closed(fake_companion: FakeCompanion) -> None:
    fake_companion.configure("typo")
    result = companion.run(TASK)
    assert companion.classify(result) == "stalled"
    assert "Unknown fake scenario" in result.stderr
