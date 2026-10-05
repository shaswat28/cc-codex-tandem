"""Command contracts, with no real login or companion calls."""

import fcntl
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

from cc_tandem import cli, companion, config
from cc_tandem.effort import Effort
from cc_tandem.lanes import LaneError, Runner
from cc_tandem.state import Phase, StateStore, TicketState, timestamp
from cc_tandem.worktree import DirectoryMutex
from tests.conftest import FakeCompanion


def fail(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Unexpected external call")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".tandem.toml").write_text('[[queue]]\nticket="T-01"\n')
    return tmp_path


def save(repo: Path, phase: Phase = Phase.RUNNING, job: str | None = "job-1") -> TicketState:
    now = timestamp()
    return StateStore(repo).put(TicketState("T-01", 0, Effort.MEDIUM, now, now, phase, job_id=job))


def test_status_missing_is_offline(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(companion, "run", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    assert cli.main(["status", "--repo", str(repo)]) == 0
    assert "No run state" in capsys.readouterr().out
    assert not (repo / ".tandem").exists()


@pytest.mark.parametrize("as_json", [False, True])
def test_status_checkpoint(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    save(repo)
    monkeypatch.setattr(companion, "run", fail)
    args = ["status", "--repo", str(repo)] + (["--json"] if as_json else [])
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    if as_json:
        assert json.loads(output)["T-01"]["job_id"] == "job-1"
    else:
        assert "running\tjob-1" in output


def test_bad_state(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runtime = cli._runtime(repo)
    (runtime / "state.json").write_text("invalid")
    assert cli.main(["status", "--repo", str(repo)]) == 1
    assert "Cannot read" in capsys.readouterr().err


def test_no_command() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main([])
    assert error.value.code == 2


def test_stop_empty(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(companion, "run", fail)
    assert cli.main(["stop", "--repo", str(repo)]) == 0
    assert "No running jobs" in capsys.readouterr().out


def test_stop_cancels_fake(
    repo: Path, offline_companion: FakeCompanion, capsys: pytest.CaptureFixture[str]
) -> None:
    offline_companion.configure("hang")
    launched = companion.run(["task", "--background", "--cwd", str(repo), "prompt"])
    assert isinstance(launched.json, dict)
    save(repo, job=str(launched.json["jobId"]))
    runtime = cli._runtime(repo)
    with (runtime / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert cli.main(["stop", "--repo", str(repo)]) == 0
    assert (runtime / "stop.request").exists()
    assert "cancelled" in capsys.readouterr().out
    jobs = json.loads(offline_companion.state_file.read_text())["jobs"]
    assert jobs[0]["status"] == "cancelled"


def test_cancel_failure(repo: Path, offline_companion: FakeCompanion) -> None:
    save(repo)
    offline_companion.configure("exit_nonzero")
    assert cli.main(["stop", "--repo", str(repo)]) == 1


def test_cancel_skips_terminal(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save(repo, Phase.DONE)
    monkeypatch.setattr(companion, "run", fail)
    assert cli.main(["stop", "--repo", str(repo)]) == 0


def test_concurrent_launch(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = cli._runtime(repo)
    monkeypatch.setattr(subprocess, "Popen", fail)
    with (runtime / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert cli.main(["run", "--repo", str(repo)]) == 1
    assert "already active" in capsys.readouterr().err


def test_direct_runner_lock(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = cli._runtime(repo)
    monkeypatch.setattr(subprocess, "Popen", fail)
    with DirectoryMutex(runtime / "run.lock").acquire():
        assert cli.main(["run", "--repo", str(repo)]) == 1


def test_foreground(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(path: Path, cfg: config.Config) -> int:
        assert path == repo
        assert cfg.queue[0].ticket == "T-01"
        assert not (path / ".tandem" / "stop.request").exists()
        return 0

    runtime = cli._runtime(repo)
    (runtime / "stop.request").touch()
    monkeypatch.setattr(cli, "_execute", execute)
    assert cli.main(["run", "--foreground", "--repo", str(repo)]) == 0


def test_invalid_config(repo: Path) -> None:
    (repo / ".tandem.toml").write_text("")
    assert cli.main(["run", "--repo", str(repo)]) == 1


@pytest.fixture
def healthy_doctor(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (repo / ".gitattributes").write_text(
        "BACKLOG.md merge=union\ndocs/AGENT-LOG.md merge=union\n"
        "docs/BACKLOG-ARCHIVE.md merge=union\n"
    )
    monkeypatch.setattr(cli, "_version", lambda: "1.2.3")

    def command(path: Path, args: list[str]) -> str:
        if args[1] == "status":
            return ""
        if args[-1] == "--git-common-dir":
            return ".git"
        return "ok"

    monkeypatch.setattr(cli, "_command", command)
    return repo


def test_doctor_healthy(healthy_doctor: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor", "--repo", str(healthy_doctor)]) == 0
    assert capsys.readouterr().out.count("\tPASS\t") == 9


@pytest.mark.parametrize(
    "check",
    [
        "companion/version",
        "login",
        "config",
        "git repo",
        "clean",
        "base branch",
        "union merges",
        "locks",
        "worktree writable",
    ],
)
def test_doctor_individual_failure(
    healthy_doctor: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    check: str,
) -> None:
    original = cli._command

    def broken() -> NoReturn:
        raise ValueError("test failure")

    def command(path: Path, args: list[str]) -> str:
        match = {
            "login": args[0] == "codex",
            "git repo": args[-1] == "--show-toplevel",
            "clean": args[1] == "status",
            "base branch": args[1] == "show-ref",
        }
        if match.get(check, False):
            broken()
        return original(path, args)

    monkeypatch.setattr(cli, "_command", command)
    funcs = {
        "companion/version": "_version",
        "union merges": "_attributes",
        "locks": "_locks",
        "worktree writable": "_writable",
    }
    if check in funcs:
        monkeypatch.setattr(cli, funcs[check], lambda *args: broken())
    if check == "config":
        (healthy_doctor / ".tandem.toml").write_text("invalid=")
    assert cli.main(["doctor", "--repo", str(healthy_doctor)]) == 1
    output = capsys.readouterr().out
    assert f"{check}\tFAIL" in output
    assert output.count("\tFAIL\t") == (2 if check == "config" else 1)


def test_doctor_dirty(healthy_doctor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = cli._command

    def command(path: Path, args: list[str]) -> str:
        return " M file" if args[1] == "status" else original(path, args)

    monkeypatch.setattr(cli, "_command", command)
    assert cli.main(["doctor", "--repo", str(healthy_doctor)]) == 1


def test_command(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, " success \n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert cli._command(repo, ["example"]) == "success"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "bad"),
    )
    with pytest.raises(ValueError, match="bad"):
        cli._command(repo, ["example"])


def test_version(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = repo / "1.2.3" / "scripts" / "companion.mjs"
    monkeypatch.setattr(companion, "locate_companion", lambda: path)
    assert cli._version().startswith("1.2.3")
    path = repo / "override" / "scripts" / "companion.mjs"
    path.parent.mkdir(parents=True)
    package = path.parent.parent / "package.json"
    package.write_text('{"version":"2.0.0"}')
    assert cli._version().startswith("2.0.0")
    package.write_text('{"version":7}')
    with pytest.raises(ValueError, match="determine"):
        cli._version()


def test_attributes_missing(repo: Path) -> None:
    (repo / ".gitattributes").write_text("# comment\nBACKLOG.md merge=union\n")
    with pytest.raises(ValueError, match="Missing"):
        cli._attributes(repo)


def test_locks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = cli._runtime(repo)
    lock = runtime / "run.lock"
    assert cli._locks(repo, repo / ".git") == "No stale locks"
    lock.mkdir()
    with pytest.raises(ValueError, match="no owner"):
        cli._locks(repo, repo / ".git")
    (lock / "owner.json").write_text('{"pid":123,"token":"test"}')
    monkeypatch.setattr(os, "kill", lambda pid, signal: None)
    assert cli._locks(repo, repo / ".git") == "No stale locks"

    def dead(pid: int, signal: int) -> NoReturn:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", dead)
    with pytest.raises(ValueError, match="Stale lock"):
        cli._locks(repo, repo / ".git")

    def denied(pid: int, signal: int) -> NoReturn:
        raise PermissionError

    monkeypatch.setattr(os, "kill", denied)
    assert cli._locks(repo, repo / ".git") == "No stale locks"


def test_writable(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli._writable(repo).endswith("-worktrees")
    monkeypatch.setattr(os, "access", lambda *args: False)
    with pytest.raises(ValueError, match="writable"):
        cli._writable(repo)


@pytest.mark.parametrize("when", ["before", "during", "none", "cancel_failure"])
def test_worker_stop(repo: Path, monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    worker = object.__new__(cli._Worker)
    worker.store = StateStore(repo)
    runtime = cli._runtime(repo)
    calls: list[list[str]] = []

    def call(self: Runner, args: list[str]) -> companion.CompanionResult:
        if when in {"during", "cancel_failure"}:
            (runtime / "stop.request").touch()
        return companion.CompanionResult(0, "", "", {"jobId": "new-job"})

    def cancel(args: list[str]) -> companion.CompanionResult:
        calls.append(args)
        return companion.CompanionResult(int(when == "cancel_failure"), "", "failed")

    monkeypatch.setattr(Runner, "_call", call)
    monkeypatch.setattr(companion, "run", cancel)
    if when == "before":
        (runtime / "stop.request").touch()
    if when == "none":
        assert worker._call(["task"]).exit_code == 0
    else:
        with pytest.raises(LaneError):
            worker._call(["task"])
    if when in {"during", "cancel_failure"}:
        assert calls == [["cancel", "new-job", "--json"]]


@pytest.mark.parametrize("phase", [Phase.DONE, Phase.FAILED])
def test_execute(repo: Path, monkeypatch: pytest.MonkeyPatch, phase: Phase) -> None:
    record = save(repo, phase)

    def init(self: cli._Worker, path: Path, cfg: config.Config, **kwargs: object) -> None:
        pass

    def run(self: cli._Worker) -> dict[str, TicketState]:
        return {record.ticket: record}

    monkeypatch.setattr(cli._Worker, "__init__", init)
    monkeypatch.setattr(cli._Worker, "run", run)
    assert cli._execute(repo, config.load(repo / ".tandem.toml")) == int(phase == Phase.FAILED)


def test_interruptible_wait(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Callable
    from typing import cast

    def init(self: cli._Worker, path: Path, cfg: config.Config, **kwargs: object) -> None:
        sleep = cast(Callable[[float], None], kwargs["sleep"])
        sleep(0)
        sleep(0.001)
        cli._runtime(repo).joinpath("stop.request").touch()
        with pytest.raises(LaneError, match="stopped"):
            sleep(1000)

    monkeypatch.setattr(cli._Worker, "__init__", init)
    monkeypatch.setattr(cli._Worker, "run", lambda self: {})
    assert cli._execute(repo, config.load(repo / ".tandem.toml")) == 0


def test_runtime_escape(repo: Path) -> None:
    outside = repo / "outside"
    outside.mkdir()
    checkout = repo / "checkout"
    checkout.mkdir()
    (checkout / ".tandem").symlink_to(outside)
    assert cli.main(["stop", "--repo", str(checkout)]) == 1


def test_detached_launch_contract(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    def spawn(args: list[str], **kwargs: object) -> SimpleNamespace:
        assert args[1:4] == ["-m", "cc_tandem.cli", "_worker"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["pass_fds"]
        with (
            (repo / ".tandem" / "launch.lock").open("a") as contender,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    assert cli.main(["run", "--repo", str(repo)]) == 0
    assert "Started runner 42. Logs:" in capsys.readouterr().out


def test_worker_entry(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_execute", lambda path, cfg: 0)
    with (cli._runtime(repo) / "launch.lock").open("a") as stream:
        fd = os.dup(stream.fileno())
        assert cli.main(["_worker", "--repo", str(repo), "--lock-fd", str(fd)]) == 0
    with pytest.raises(OSError):
        os.fstat(fd)


def test_execute_ignores_historical_failure(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = save(repo, Phase.DONE)
    old = replace(record, ticket="T-99", phase=Phase.FAILED)

    def init(self: cli._Worker, path: Path, cfg: config.Config, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(cli._Worker, "__init__", init)
    monkeypatch.setattr(cli._Worker, "run", lambda self: {"T-01": record, "T-99": old})
    assert cli._execute(repo, config.load(repo / ".tandem.toml")) == 0
