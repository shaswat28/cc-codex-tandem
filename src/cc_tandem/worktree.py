"""Local worktree lifecycle and serialised, recoverable merges.

Construct a manager with the checkout of the base branch. Worktrees default to a
sibling directory so generated lane files cannot accidentally enter base commits.
Call ``sync_base_into`` before ``merge_into_base``; both return inspectable conflict
results and retain the ticket branch. No operation pushes or force-removes work.
"""

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LOGGER = logging.getLogger(__name__)


class WorktreeError(RuntimeError):
    """The requested lifecycle operation cannot be completed safely."""


class GitError(WorktreeError):
    """A git command failed for a reason other than a merge conflict."""

    def __init__(self, cwd: Path, result: subprocess.CompletedProcess[str]) -> None:
        self.cwd = cwd
        self.exit_code = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        super().__init__(f"Git failed in {cwd} ({result.returncode}): {result.stderr.strip()}")


class LockTimeoutError(WorktreeError):
    """Another process or thread still owns the merge mutex."""


@dataclass(frozen=True)
class MergeResult:
    status: Literal["merged", "conflict"]
    operation: Literal["sync", "merge"]
    ticket: str
    conflicts: tuple[str, ...] = ()


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise GitError(cwd, result)
    return result


def commit_all(directory: Path, message: str) -> bool:
    """Stage all changes and commit them; return False for a clean checkout."""
    _git(directory, "add", "--all")
    diff = _git(directory, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return False
    if diff.returncode != 1:
        raise GitError(directory, diff)
    _git(directory, "commit", "-m", message)
    return True


class DirectoryMutex:
    """A directory records the owning PID; dead owners are reclaimed and logged.

    A short advisory guard protects directory creation, owner publication and
    reclamation from each other. It is never held while doing git work. Keeping
    the guard file avoids replacing its inode underneath concurrent contenders.
    """

    def __init__(self, path: Path, *, timeout: float = 60.0) -> None:
        if timeout < 0:
            raise ValueError("Lock timeout must be non-negative")
        self.path = path
        self.timeout = timeout

    @contextmanager
    def _guard(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".guard").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _owner(self) -> tuple[int, str] | None:
        try:
            data = json.loads((self.path / "owner.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise WorktreeError(f"Cannot read lock owner at {self.path}: {exc}") from exc
        if (
            not isinstance(data, dict)
            or type(data.get("pid")) is not int
            or data["pid"] <= 0
            or not isinstance(data.get("token"), str)
        ):
            raise WorktreeError(f"Invalid lock owner at {self.path}")
        return data["pid"], data["token"]

    def _reclaim(self) -> bool:
        owner = self._owner()
        if owner is not None:
            try:
                os.kill(owner[0], 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                return False
            else:
                return False
        shutil.rmtree(self.path)
        LOGGER.warning(
            "Reclaimed stale lock %s (owning pid %s)",
            self.path,
            owner[0] if owner else "unpublished",
        )
        return True

    @contextmanager
    def acquire(self) -> Iterator[None]:
        token = uuid.uuid4().hex
        deadline = time.monotonic() + self.timeout
        while True:
            acquired = False
            with self._guard():
                if self.path.exists():
                    self._reclaim()
                if not self.path.exists():
                    self.path.mkdir()
                    try:
                        pending = self.path / "owner.tmp"
                        pending.write_text(json.dumps({"pid": os.getpid(), "token": token}))
                        pending.replace(self.path / "owner.json")
                    except BaseException:
                        shutil.rmtree(self.path)
                        raise
                    acquired = True
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"Timed out waiting for lock {self.path}")
            time.sleep(0.02)
        try:
            yield
        finally:
            with self._guard():
                if self._owner() == (os.getpid(), token):
                    shutil.rmtree(self.path)


class WorktreeManager:
    """Bind ticket operations to a base checkout and a worktree directory."""

    def __init__(
        self,
        repo: Path,
        *,
        base: str = "main",
        worktree_root: Path | None = None,
        lock_timeout: float = 60.0,
    ) -> None:
        self.repo = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if (
            base.startswith("-")
            or _git(self.repo, "check-ref-format", f"refs/heads/{base}", check=False).returncode
        ):
            raise WorktreeError(f"Invalid base branch: {base!r}")
        self.base = base
        self.worktree_root = (
            worktree_root.resolve()
            if worktree_root is not None
            else self.repo.parent / f"{self.repo.name}-worktrees"
        )
        common = Path(_git(self.repo, "rev-parse", "--git-common-dir").stdout.strip())
        common = (self.repo / common).resolve()
        self.mutex = DirectoryMutex(common / "tandem-merge.lock", timeout=lock_timeout)

    def path_for(self, ticket: str) -> Path:
        """Resolve only single-component ticket names that are valid branches."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", ticket):
            raise WorktreeError(f"Invalid ticket branch: {ticket!r}")
        if _git(self.repo, "check-ref-format", "--branch", ticket, check=False).returncode:
            raise WorktreeError(f"Invalid ticket branch: {ticket!r}")
        if ticket == self.base:
            raise WorktreeError("Ticket branch must differ from the base branch")
        path = self.worktree_root / ticket
        if path.resolve() != path:
            raise WorktreeError(f"Worktree path is a symlink: {path}")
        return path

    def _worktrees(self) -> dict[Path, str | None]:
        records: dict[Path, str | None] = {}
        path: Path | None = None
        for field in _git(self.repo, "worktree", "list", "--porcelain", "-z").stdout.split("\0"):
            if field.startswith("worktree "):
                path = Path(field.removeprefix("worktree ")).resolve()
                records[path] = None
            elif path is not None and field.startswith("branch refs/heads/"):
                records[path] = field.removeprefix("branch refs/heads/")
        return records

    def create(self, ticket: str, base: str | None = None) -> Path:
        """Create or resume a ticket branch based on the configured base branch."""
        if base is not None and base != self.base:
            raise WorktreeError(
                f"Requested base {base!r} differs from configured base {self.base!r}"
            )
        path = self.path_for(ticket)
        with self.mutex.acquire():
            records = self._worktrees()
            if path in records:
                if records[path] != ticket or not path.is_dir():
                    raise WorktreeError(f"Worktree registration at {path} cannot be reused")
                return path
            if ticket in records.values():
                raise WorktreeError(f"Ticket branch {ticket} is already checked out elsewhere")
            self.worktree_root.mkdir(parents=True, exist_ok=True)
            exists = _git(
                self.repo, "show-ref", "--verify", "--quiet", f"refs/heads/{ticket}", check=False
            )
            if exists.returncode == 0:
                _git(self.repo, "worktree", "add", str(path), ticket)
            elif exists.returncode == 1:
                _git(
                    self.repo, "worktree", "add", "-b", ticket, str(path), f"refs/heads/{self.base}"
                )
            else:
                raise GitError(self.repo, exists)
        return path

    def commit_all(self, directory: Path, message: str) -> bool:
        """Commit a lane checkout belonging to this manager."""
        directory = directory.resolve()
        records = self._worktrees()
        if directory == self.repo or directory not in records:
            raise WorktreeError(f"Not a managed ticket worktree: {directory}")
        ticket = records[directory]
        if ticket is None or directory != self.path_for(ticket):
            raise WorktreeError(f"Not a managed ticket worktree: {directory}")
        return commit_all(directory, message)

    def _checkout(self, directory: Path, branch: str) -> None:
        current = _git(directory, "symbolic-ref", "--quiet", "HEAD").stdout.strip()
        if current != f"refs/heads/{branch}":
            raise WorktreeError(f"Expected {branch} checked out at {directory}, found {current}")
        merge_head = Path(_git(directory, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
        if (directory / merge_head).exists():
            _git(directory, "merge", "--abort")
            LOGGER.warning("Aborted interrupted merge in %s", directory)
        if _git(directory, "status", "--porcelain").stdout:
            raise WorktreeError(f"Checkout must be clean before merging: {directory}")

    def _merge(
        self, directory: Path, source: str, ticket: str, operation: Literal["sync", "merge"]
    ) -> MergeResult:
        args = ["merge", "--commit", "--no-edit", "--no-ff" if operation == "merge" else "--ff"]
        try:
            result = _git(directory, *args, f"refs/heads/{source}", check=False)
            if result.returncode == 0:
                return MergeResult("merged", operation, ticket)
            conflicts = tuple(
                name
                for name in _git(
                    directory, "diff", "--name-only", "--diff-filter=U", "-z"
                ).stdout.split("\0")
                if name
            )
        finally:
            merge_head = Path(
                _git(directory, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
            )
            if (directory / merge_head).exists():
                _git(directory, "merge", "--abort")
        if conflicts:
            return MergeResult("conflict", operation, ticket, conflicts)
        raise GitError(directory, result)

    def _ticket_checkout(self, path: Path, ticket: str) -> None:
        if self._worktrees().get(path) != ticket:
            raise WorktreeError(f"Not a registered worktree for ticket {ticket}: {path}")
        self._checkout(path, ticket)

    def sync_base_into(self, ticket: str) -> MergeResult:
        """Merge the current base into a clean lane, aborting conflicts."""
        path = self.path_for(ticket)
        with self.mutex.acquire():
            self._checkout(self.repo, self.base)
            self._ticket_checkout(path, ticket)
            return self._merge(path, self.base, ticket, "sync")

    def merge_into_base(self, ticket: str) -> MergeResult:
        """Serialise a --no-ff merge into base, recovering interrupted merges."""
        path = self.path_for(ticket)
        with self.mutex.acquire():
            self._checkout(self.repo, self.base)
            self._ticket_checkout(path, ticket)
            return self._merge(self.repo, ticket, ticket, "merge")

    def remove(self, ticket: str) -> None:
        """Remove a clean worktree, retaining its branch; missing is a no-op."""
        path = self.path_for(ticket)
        with self.mutex.acquire():
            records = self._worktrees()
            if path not in records:
                if path.exists():
                    raise WorktreeError(f"Refusing to remove unregistered directory {path}")
                return
            if records[path] != ticket:
                raise WorktreeError(f"Worktree at {path} is not on ticket branch {ticket}")
            _git(self.repo, "worktree", "remove", str(path))
