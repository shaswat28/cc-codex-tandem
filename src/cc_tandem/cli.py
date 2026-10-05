"""User commands and a detached worker; exit codes: 0 success, 1 failure, 2 usage."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryFile

from cc_tandem import companion, config
from cc_tandem.lanes import LaneError, Runner
from cc_tandem.state import Phase, StateStore, TicketState
from cc_tandem.worktree import DirectoryMutex, LockTimeoutError

TERMINAL = {Phase.DONE, Phase.SKIPPED, Phase.BLOCKED, Phase.CONFLICT, Phase.EXHAUSTED, Phase.FAILED}


def _command(repo: Path, args: Sequence[str]) -> str:
    result = subprocess.run(args, cwd=repo, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


def _runtime(repo: Path) -> Path:
    store = StateStore(repo)
    store._contained(store.path.parent)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    for name in ("launch.lock", "stop.request", "run.lock", "run.lock.guard", "logs"):
        store._contained(store.path.parent / name)
    return store.path.parent


def _cancel(records: dict[str, TicketState]) -> None:
    failures: list[str] = []
    for job in sorted(
        {
            r.job_id
            for r in records.values()
            if r.job_id and (r.phase not in TERMINAL or r.phase == Phase.FAILED)
        }
    ):
        result = companion.run(["cancel", job, "--json"])
        if result.exit_code:
            failures.append(f"{job}: {result.stderr or result.stdout}")
    if failures:
        raise LaneError("Cancellation failed: " + "; ".join(failures))


class _Worker(Runner):
    """Observe a cooperative stop before calls and during retry waits."""

    def _stopping(self) -> bool:
        return (self.store.path.parent / "stop.request").exists()

    def _call(self, args: Sequence[str]) -> companion.CompanionResult:
        if self._stopping():
            _cancel(self.store.read())
            raise LaneError("Run stopped by user")
        result = super()._call(args)
        if self._stopping():
            # A stop may arrive while submission is publishing its job ID.
            if args[0] == "task" and isinstance(result.json, dict):
                job = result.json.get("jobId")
                if isinstance(job, str):
                    cancelled = companion.run(["cancel", job, "--json"])
                    if cancelled.exit_code:
                        raise LaneError(f"Cancellation failed for {job}: {cancelled.stderr}")
            _cancel(self.store.read())
            raise LaneError("Run stopped by user")
        return result


def _execute(repo: Path, cfg: config.Config) -> int:
    marker = repo / ".tandem" / "stop.request"

    def sleep(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if marker.exists():
                _cancel(StateStore(repo).read())
                raise LaneError("Run stopped by user")
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    records = _Worker(repo, cfg, sleep=sleep).run()
    queued = {entry.ticket for entry in cfg.queue}
    return int(
        any(
            record.phase not in {Phase.DONE, Phase.SKIPPED}
            for ticket, record in records.items()
            if ticket in queued
        )
    )


def _run(repo: Path, cfg_path: Path, foreground: bool) -> int:
    cfg = config.load(cfg_path, for_run=True)
    runtime = _runtime(repo)
    guard = runtime / "launch.lock"
    StateStore(repo)._contained(guard)
    with guard.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LaneError("A run is already active; use tandem status or tandem stop") from exc
        # Respect runners started directly through the public Runner API too.
        try:
            with DirectoryMutex(runtime / "run.lock", timeout=0).acquire():
                pass
        except LockTimeoutError as exc:
            raise LaneError("A run is already active; use tandem status or tandem stop") from exc
        (runtime / "stop.request").unlink(missing_ok=True)
        if foreground:
            return _execute(repo, cfg)
        log = runtime / "logs" / f"run-{uuid.uuid4().hex}.log"
        StateStore(repo)._contained(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as output:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cc_tandem.cli",
                    "_worker",
                    "--repo",
                    str(repo),
                    "--config",
                    str(cfg_path),
                    "--lock-fd",
                    str(stream.fileno()),
                ],
                cwd=repo,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(stream.fileno(),),
            )
        print(f"Started runner {child.pid}. Logs: {log}")
    return 0


def _status(repo: Path, as_json: bool) -> int:
    records = StateStore(repo).read()
    if as_json:
        from dataclasses import asdict

        print(json.dumps({key: asdict(value) for key, value in records.items()}, indent=2))
    elif not records:
        print("No run state found.")
    else:
        print("TICKET\tLANE\tPHASE\tJOB\tERROR")
        for record in records.values():
            print(
                f"{record.ticket}\t{record.lane}\t{record.phase}\t"
                f"{record.job_id or '-'}\t{record.error or '-'}"
            )
    return 0


def _stop(repo: Path) -> int:
    records = StateStore(repo).read()
    runtime = _runtime(repo)
    active = False
    with (runtime / "launch.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
            (runtime / "stop.request").touch()
        _cancel(records)
    print(
        "Stop requested; known active jobs cancelled."
        if active
        or any(
            r.job_id and (r.phase not in TERMINAL or r.phase == Phase.FAILED)
            for r in records.values()
        )
        else "No running jobs."
    )
    return 0


def _version() -> str:
    path = companion.locate_companion()
    version = path.parent.parent.name
    if companion.VERSION_PATTERN.fullmatch(version):
        return f"{version} ({path})"
    package = json.loads((path.parent.parent / "package.json").read_text())
    metadata_version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(metadata_version, str) or not companion.VERSION_PATTERN.fullmatch(
        metadata_version
    ):
        raise ValueError(f"Cannot determine companion version at {path}")
    return f"{metadata_version} ({path})"


def _attributes(repo: Path) -> str:
    entries = (repo / ".gitattributes").read_text().splitlines()
    for name in ("BACKLOG.md", "docs/AGENT-LOG.md", "docs/BACKLOG-ARCHIVE.md"):
        matches = [line.split() for line in entries if line.split() and line.split()[0] == name]
        if not matches or "merge=union" not in matches[-1][1:]:
            raise ValueError(f"Missing {name} merge=union")
    return "Union merge entries present"


def _locks(repo: Path, common: Path) -> str:
    for path in (repo / ".tandem" / "run.lock", common / "tandem-merge.lock"):
        if path.exists():
            owner = DirectoryMutex(path)._owner()
            if owner is None:
                raise ValueError(f"Stale lock: {path} (no owner)")
            try:
                os.kill(owner[0], 0)
            except ProcessLookupError as exc:
                raise ValueError(f"Stale lock: {path}") from exc
            except PermissionError:
                pass
    return "No stale locks"


def _writable(repo: Path) -> str:
    target = repo.parent / f"{repo.name}-worktrees"
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise ValueError(f"Worktree directory is not writable: {target}")
    with TemporaryFile(dir=ancestor):
        pass
    return str(target)


def _doctor(repo: Path, cfg_path: Path) -> int:
    failed = False
    cfg: config.Config | None = None

    def parsed() -> str:
        nonlocal cfg
        cfg = config.load(cfg_path)
        return f"{cfg_path}: {cfg.lanes.count} lanes, base {cfg.lanes.base}"

    def clean() -> str:
        if _command(repo, ["git", "status", "--porcelain"]):
            raise ValueError("Repository has uncommitted changes")
        return "Clean"

    def base() -> str:
        if cfg is None:
            raise ValueError("Cannot check base: config is invalid")
        return _command(repo, ["git", "show-ref", "--verify", f"refs/heads/{cfg.lanes.base}"])

    checks: tuple[tuple[str, Callable[[], str]], ...] = (
        ("companion/version", _version),
        ("login", lambda: _command(repo, ["codex", "login", "status"])),
        ("config", parsed),
        ("git repo", lambda: _command(repo, ["git", "rev-parse", "--show-toplevel"])),
        ("clean", clean),
        ("base branch", base),
        ("union merges", lambda: _attributes(repo)),
        ("locks", lambda: _locks(repo, _common(repo))),
        ("worktree writable", lambda: _writable(repo)),
    )
    print("CHECK\tRESULT\tDETAIL")
    for name, check in checks:
        try:
            detail = check()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failed = True
            print(f"{name}\tFAIL\t{exc}")
        else:
            print(f"{name}\tPASS\t{detail}")
    return int(failed)


def _common(repo: Path) -> Path:
    return (repo / _command(repo, ["git", "rev-parse", "--git-common-dir"])).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "status", "stop", "doctor", "_worker"):
        command = subcommands.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path.cwd())
        if name in {"run", "doctor", "_worker"}:
            command.add_argument("--config", type=Path, default=Path(".tandem.toml"))
        if name == "run":
            command.add_argument("--foreground", action="store_true")
        if name == "status":
            command.add_argument("--json", action="store_true")
        if name == "_worker":
            command.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == "status":
            return _status(repo, args.json)
        if args.command == "stop":
            return _stop(repo)
        cfg_path = (repo / args.config).resolve()
        if args.command == "doctor":
            return _doctor(repo, cfg_path)
        if args.command == "_worker":
            with os.fdopen(args.lock_fd, "a"):
                return _execute(repo, config.load(cfg_path, for_run=True))
        return _run(repo, cfg_path, args.foreground)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"tandem: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
