"""Exercise companion discovery and invocation without calling an installation."""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest

from cc_tandem import companion
from cc_tandem.backlog import Status
from cc_tandem.companion import (
    CompanionInvalid,
    CompanionNotFound,
    CompanionResult,
    StatusRecord,
)
from cc_tandem.effort import FailureClass


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CC_TANDEM_COMPANION", raising=False)
    monkeypatch.delenv("CC_TANDEM_DRY_RUN", raising=False)
    monkeypatch.setattr(
        "cc_tandem.companion.subprocess.run", Mock(side_effect=AssertionError("spawn"))
    )


def install(home: Path, version: str) -> Path:
    path = home / companion.SEARCH_PATTERN.replace("*", version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// fake fixture\n")
    return path


@pytest.mark.parametrize(
    ("versions", "winner"),
    [
        (["1.0.9", "1.0.10", "latest", "1.0.010", "9.1", "v9.0.0"], "1.0.10"),
        (["1.9.99", "1.10.0", "2.0.0"], "2.0.0"),
        (["1.0.0-rc.2", "1.0.0-rc.10", "1.0.0"], "1.0.0"),
        (["1.0.0-beta.9", "1.0.0-beta.10", "1.0.0-beta.01"], "1.0.0-beta.10"),
        (["1.0.0-1", "1.0.0-alpha", "1.0.0-alpha.1"], "1.0.0-alpha.1"),
        (["1.0.0+build.1", "0.9.9"], "1.0.0+build.1"),
    ],
)
def test_version_ordering(tmp_path: Path, versions: list[str], winner: str) -> None:
    paths = {version: install(tmp_path, version) for version in versions}
    assert companion.locate_companion() == paths[winner]


def test_missing_installation(tmp_path: Path) -> None:
    install(tmp_path, "invalid")
    with pytest.raises(CompanionNotFound, match="searched") as error:
        companion.locate_companion()
    assert str(tmp_path / companion.SEARCH_PATTERN) in str(error.value)


def test_directory_is_not_companion(tmp_path: Path) -> None:
    path = tmp_path / companion.SEARCH_PATTERN.replace("*", "1.0.0")
    path.mkdir(parents=True)
    with pytest.raises(CompanionNotFound):
        companion.locate_companion()


def test_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "explicit.mjs"
    path.touch()
    install(tmp_path, "9.0.0")
    monkeypatch.setenv("CC_TANDEM_COMPANION", "~/explicit.mjs")
    assert companion.locate_companion() == path


@pytest.mark.parametrize("override", ["missing.mjs", "", "."])
def test_invalid_override(monkeypatch: pytest.MonkeyPatch, override: str) -> None:
    monkeypatch.setenv("CC_TANDEM_COMPANION", override)
    with pytest.raises(CompanionInvalid, match="CC_TANDEM_COMPANION"):
        companion.locate_companion()


@pytest.mark.parametrize(
    ("stdout", "parsed", "exit_code"),
    [
        ('{"ok": true}', {"ok": True}, 0),
        ("plain text", None, 0),
        ("[1]", [1], 7),
        ("null", None, 0),
        ("", None, 1),
    ],
)
def test_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
    parsed: companion.JsonValue,
    exit_code: int,
) -> None:
    path = install(tmp_path, "1.0.0")
    spawn = Mock(return_value=subprocess.CompletedProcess([], exit_code, stdout, "diagnostic"))
    monkeypatch.setattr("cc_tandem.companion.subprocess.run", spawn)
    args = ["exec", "a prompt with spaces; $(ignored)"]
    result = companion.run(args)
    assert result == CompanionResult(
        exit_code, stdout, "diagnostic", parsed, ("node", str(path), *args)
    )
    spawn.assert_called_once_with(result.command, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "outcome"),
    [
        ("done", "", 0, "ok"),
        ("", "failure", 1, "stalled"),
        ("You've hit your usage limit", "", 1, "capacity"),
        ("", "Rate limit exceeded", 1, "capacity"),
        ("quota exhausted", "", 1, "capacity"),
        ("Too many requests", "", 1, "capacity"),
        ('{"error":"usage_limit"}', "", 1, "capacity"),
    ],
)
def test_classify(stdout: str, stderr: str, code: int, outcome: FailureClass) -> None:
    assert (
        companion.classify(CompanionResult(code, stdout, stderr), ticket_status=Status.DONE)
        == outcome
    )


@pytest.mark.parametrize("override", [None, "/does/not/exist.mjs"])
def test_dry_run(monkeypatch: pytest.MonkeyPatch, override: str | None) -> None:
    if override is not None:
        monkeypatch.setenv("CC_TANDEM_COMPANION", override)
    monkeypatch.setenv("CC_TANDEM_DRY_RUN", "1")
    spawn = Mock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr("cc_tandem.companion.subprocess.run", spawn)
    result = companion.run(["exec", "prompt"])
    assert result.dry_run
    assert result.command == ("node", override or companion.SEARCH_PATTERN, "exec", "prompt")
    assert companion.classify(result) == "ok"
    assert companion.status_all() == companion.CompanionStatus([], [])
    spawn.assert_not_called()


@pytest.mark.parametrize("value", [{}, {"running": [], "recent": []}, {"recent": []}])
def test_empty_status(monkeypatch: pytest.MonkeyPatch, value: companion.JsonValue) -> None:
    mock = Mock(return_value=CompanionResult(0, "", "", value))
    monkeypatch.setattr(companion, "run", mock)
    assert companion.status_all() == companion.CompanionStatus([], [])
    mock.assert_called_once_with(["status", "--all", "--json"])


def test_status_records(monkeypatch: pytest.MonkeyPatch) -> None:
    value: companion.JsonValue = {
        "running": [{"id": "job-1", "status": "running", "extra": 3}],
        "recent": [{"id": "job-2", "status": "done", "summary": "Finished"}],
        "unknown": True,
    }
    monkeypatch.setattr(companion, "run", Mock(return_value=CompanionResult(0, "", "", value)))
    assert companion.status_all() == companion.CompanionStatus(
        [StatusRecord("job-1", "running")], [StatusRecord("job-2", "done", "Finished")]
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"running": None},
        {"recent": [3]},
        {"running": [{}]},
        {"recent": [{"id": "j", "status": "done", "summary": 7}]},
    ],
)
def test_invalid_status(monkeypatch: pytest.MonkeyPatch, value: companion.JsonValue) -> None:
    monkeypatch.setattr(companion, "run", Mock(return_value=CompanionResult(0, "", "", value)))
    with pytest.raises(CompanionInvalid):
        companion.status_all()


def test_failed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(args: Sequence[str]) -> CompanionResult:
        return CompanionResult(5, "", "unavailable")

    monkeypatch.setattr(companion, "run", failed)
    with pytest.raises(CompanionInvalid, match=r"failed \(5\): unavailable"):
        companion.status_all()


def test_successful_exit_is_never_a_usage_limit() -> None:
    """A job that succeeds while printing a limit phrase must not be retried."""
    result = companion.CompanionResult(
        exit_code=0,
        stdout="Implemented the rate limit middleware; too many requests now return 429.",
        stderr="",
    )
    assert companion.classify(result, ticket_status=Status.DONE) == "ok"


def test_usage_limit_is_detected_only_on_failure() -> None:
    result = companion.CompanionResult(exit_code=1, stdout="", stderr="You hit your usage limit")
    assert companion.classify(result) == "capacity"


@pytest.mark.parametrize(
    ("code", "diff", "started", "gate", "status", "failure", "previous", "expected"),
    [
        (0, False, True, None, None, None, None, FailureClass.STALLED),
        (1, False, True, None, None, None, None, FailureClass.STALLED),
        (1, False, False, None, None, None, None, FailureClass.INFRASTRUCTURE),
        (0, False, False, None, None, None, None, FailureClass.INFRASTRUCTURE),
        (1, True, False, False, None, None, None, FailureClass.IMPLEMENTATION),
        (0, True, True, False, Status.DONE, None, None, FailureClass.IMPLEMENTATION),
        (1, True, True, False, None, "check:a", "check:b", FailureClass.IMPLEMENTATION),
        (1, True, True, False, None, "check:a", "check:a", FailureClass.STALLED),
        (1, True, True, False, None, "", "", FailureClass.IMPLEMENTATION),
        (0, True, True, True, Status.DONE, "check:a", "check:a", FailureClass.OK),
        (0, False, True, None, Status.DONE, None, None, FailureClass.OK),
        (1, True, True, True, Status.DONE, None, None, FailureClass.STALLED),
        (0, True, True, None, Status.TODO, None, None, FailureClass.STALLED),
        (0, True, True, True, Status.IN_PROGRESS, None, None, FailureClass.STALLED),
        (1, False, True, False, None, "check:a", "check:a", FailureClass.STALLED),
        (1, True, False, False, Status.BLOCKED, "a", "a", FailureClass.OWNER_DECISION),
        (0, False, True, None, Status.BLOCKED, None, None, FailureClass.OWNER_DECISION),
    ],
)
def test_evidence_classification(
    code: int,
    diff: bool,
    started: bool,
    gate: bool | None,
    status: Status | None,
    failure: str | None,
    previous: str | None,
    expected: FailureClass,
) -> None:
    result = CompanionResult(code, "", "")
    assert (
        companion.classify(
            result,
            has_diff=diff,
            turn_started=started,
            gate_passed=gate,
            ticket_status=status,
            gate_failure=failure,
            previous_gate_failure=previous,
        )
        is expected
    )


def test_block_takes_precedence_over_capacity() -> None:
    assert (
        companion.classify(
            CompanionResult(1, "usage limit", ""),
            ticket_status=Status.BLOCKED,
        )
        is FailureClass.OWNER_DECISION
    )


def test_capacity_takes_precedence_over_gate_failure() -> None:
    assert (
        companion.classify(
            CompanionResult(1, "usage limit", ""),
            has_diff=True,
            gate_passed=False,
            gate_failure="a",
            previous_gate_failure="a",
        )
        is FailureClass.CAPACITY
    )


def test_output_does_not_establish_completion_or_owner_decision() -> None:
    assert (
        companion.classify(
            CompanionResult(0, "Status: DONE\nStatus: BLOCKED (needs owner decision)", ""),
        )
        is FailureClass.STALLED
    )


def test_process_spawn_error_is_inspectable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = install(tmp_path, "1.0.0")
    monkeypatch.setattr(
        "cc_tandem.companion.subprocess.run", Mock(side_effect=FileNotFoundError("node missing"))
    )
    result = companion.run(["task", "prompt"])
    assert result == CompanionResult(
        1,
        "",
        "node missing",
        command=("node", str(path), "task", "prompt"),
        process_error=True,
    )
    assert companion.classify(result) is FailureClass.INFRASTRUCTURE
    # Existing edits and a failed gate still supply implementation evidence.
    assert (
        companion.classify(result, has_diff=True, gate_passed=False) is FailureClass.IMPLEMENTATION
    )
