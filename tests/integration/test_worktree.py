"""Exercise lifecycle operations against real, isolated temporary repositories."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cc_tandem import worktree
from cc_tandem.worktree import (
    DirectoryMutex,
    GitError,
    LockTimeoutError,
    WorktreeError,
    WorktreeManager,
    commit_all,
)

pytestmark = pytest.mark.integration


def git(directory: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True, check=False
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorktreeManager:
    # Keep identities, hooks, signing, global ignores and git environment out of
    # the real user's settings. Every git command targets a temporary checkout.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    repo = tmp_path / "example repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "shared.txt").write_text("initial\n")
    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Initial content")
    return WorktreeManager(repo)


def change(manager: WorktreeManager, ticket: str, filename: str, content: str) -> Path:
    path = manager.create(ticket, "main")
    (path / filename).write_text(content)
    assert manager.commit_all(path, f"Implement {ticket}")
    return path


def assert_clean(directory: Path) -> None:
    assert git(directory, "status", "--porcelain") == ""
    merge_head = Path(git(directory, "rev-parse", "--git-path", "MERGE_HEAD"))
    assert not (directory / merge_head).exists()


def test_happy_path_with_sync_and_no_ff_merge(manager: WorktreeManager) -> None:
    path = change(manager, "T-01", "lane.txt", "lane content\n")
    (manager.repo / "base.txt").write_text("base advanced\n")
    assert commit_all(manager.repo, "Advance base")
    assert manager.sync_base_into("T-01").status == "merged"
    assert (path / "base.txt").read_text() == "base advanced\n"
    result = manager.merge_into_base("T-01")
    assert (result.status, result.operation, result.ticket) == ("merged", "merge", "T-01")
    assert result.conflicts == ()
    assert (manager.repo / "lane.txt").read_text() == "lane content\n"
    assert len(git(manager.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    assert_clean(manager.repo)
    assert_clean(path)
    manager.remove("T-01")
    assert not path.exists()
    assert git(manager.repo, "rev-parse", "T-01")


def test_fast_forward_still_creates_merge_commit(manager: WorktreeManager) -> None:
    change(manager, "T-01", "lane.txt", "new\n")
    assert manager.merge_into_base("T-01").status == "merged"
    assert len(git(manager.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


def test_commit_no_op_and_deletions(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    head = git(path, "rev-parse", "HEAD")
    assert not manager.commit_all(path, "Nothing changed")
    assert git(path, "rev-parse", "HEAD") == head
    (path / "shared.txt").unlink()
    assert manager.commit_all(path, "Remove old content")
    assert git(path, "ls-files") == ""
    assert_clean(path)


@pytest.mark.parametrize("operation", ["merge", "sync"])
def test_conflict_is_aborted_and_branches_preserved(
    manager: WorktreeManager, operation: str
) -> None:
    path = change(manager, "T-01", "shared.txt", "lane\n")
    (manager.repo / "shared.txt").write_text("base\n")
    commit_all(manager.repo, "Change base")
    base_head = git(manager.repo, "rev-parse", "HEAD")
    ticket_head = git(path, "rev-parse", "HEAD")
    result = (
        manager.merge_into_base("T-01") if operation == "merge" else manager.sync_base_into("T-01")
    )
    assert (result.status, result.operation, result.ticket) == ("conflict", operation, "T-01")
    assert result.conflicts == ("shared.txt",)
    assert git(manager.repo, "rev-parse", "HEAD") == base_head
    assert git(path, "rev-parse", "HEAD") == ticket_head
    assert (manager.repo / "shared.txt").read_text() == "base\n"
    assert (path / "shared.txt").read_text() == "lane\n"
    assert_clean(manager.repo)
    assert_clean(path)
    assert not manager.mutex.path.exists()


def test_existing_branch_is_reused_and_create_is_idempotent(manager: WorktreeManager) -> None:
    path = change(manager, "T-01", "saved.txt", "previous attempt\n")
    head = git(path, "rev-parse", "HEAD")
    assert manager.create("T-01") == path
    manager.remove("T-01")
    manager.remove("T-01")
    assert manager.create("T-01") == path
    assert git(path, "rev-parse", "HEAD") == head
    assert (path / "saved.txt").read_text() == "previous attempt\n"
    assert git(manager.repo, "branch", "--list", "T-01").count("T-01") == 1


def test_already_deleted_worktree_can_be_removed(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    shutil.rmtree(path)
    manager.remove("T-01")
    manager.remove("T-01")
    assert str(path) not in git(manager.repo, "worktree", "list", "--porcelain")
    assert manager.create("T-01") == path


@pytest.mark.parametrize("operation", ["merge", "sync"])
def test_interrupted_merge_recovers(
    manager: WorktreeManager, operation: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = change(manager, "T-01", "lane.txt", "lane\n")
    (manager.repo / "base.txt").write_text("base\n")
    commit_all(manager.repo, "Advance base")
    target, source = (manager.repo, "T-01") if operation == "merge" else (path, "main")
    git(target, "merge", "--no-commit", "--no-ff", source)
    merge_head = Path(git(target, "rev-parse", "--git-path", "MERGE_HEAD"))
    assert (target / merge_head).exists()
    result = (
        manager.merge_into_base("T-01") if operation == "merge" else manager.sync_base_into("T-01")
    )
    assert result.status == "merged"
    assert "Aborted interrupted merge" in caplog.text
    assert_clean(target)
    assert (target / "lane.txt").exists()
    assert (target / "base.txt").exists()


def test_conflicted_crash_can_recover_and_retry(manager: WorktreeManager) -> None:
    path = change(manager, "T-01", "shared.txt", "lane\n")
    (manager.repo / "shared.txt").write_text("base\n")
    commit_all(manager.repo, "Change base")
    git(manager.repo, "merge", "--no-ff", "T-01", check=False)
    assert git(manager.repo, "diff", "--name-only", "--diff-filter=U") == "shared.txt"
    assert manager.merge_into_base("T-01").status == "conflict"
    assert_clean(manager.repo)
    assert_clean(path)


def dead_pid() -> int:
    completed = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(completed.stdout)


def test_stale_lock_is_reclaimed(
    manager: WorktreeManager, caplog: pytest.LogCaptureFixture
) -> None:
    manager.mutex.path.mkdir()
    (manager.mutex.path / "owner.json").write_text(json.dumps({"pid": dead_pid(), "token": "old"}))
    assert manager.create("T-01").exists()
    assert "Reclaimed stale lock" in caplog.text
    assert not manager.mutex.path.exists()


def test_crash_before_owner_publication_is_reclaimed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lock = DirectoryMutex(tmp_path / "mutex")
    lock.path.mkdir()
    with lock.acquire():
        assert lock.path.is_dir()
    assert "Reclaimed stale lock" in caplog.text
    assert not lock.path.exists()


def test_live_owner_times_out_and_exception_releases(tmp_path: Path) -> None:
    first = DirectoryMutex(tmp_path / "mutex")
    second = DirectoryMutex(first.path, timeout=0)
    with pytest.raises(RuntimeError, match="body failed"), first.acquire():
        with pytest.raises(LockTimeoutError), second.acquire():
            pytest.fail("A live lock must not be acquired")
        raise RuntimeError("body failed")
    assert not first.path.exists()
    with second.acquire():
        assert first.path.exists()


@pytest.mark.parametrize("owner", ["bad json", "[]", '{"pid": 0, "token": "x"}', '{"pid": 1}'])
def test_invalid_owner_is_preserved(tmp_path: Path, owner: str) -> None:
    lock = DirectoryMutex(tmp_path / "mutex")
    lock.path.mkdir()
    (lock.path / "owner.json").write_text(owner)
    with pytest.raises(WorktreeError, match="lock owner"), lock.acquire():
        pytest.fail("A malformed lock must not be stolen")
    assert (lock.path / "owner.json").read_text() == owner


def wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail(f"Timed out waiting for {path}")
        time.sleep(0.01)


def test_two_merges_are_serialised_through_directory_lock(
    manager: WorktreeManager, tmp_path: Path
) -> None:
    change(manager, "T-01", "first.txt", "first\n")
    change(manager, "T-02", "second.txt", "second\n")
    other = WorktreeManager(manager.repo)
    assert manager.mutex.path == other.mutex.path
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    calls = tmp_path / "calls"
    hook = manager.repo / ".git" / "hooks" / "pre-merge-commit"
    # Hold the first real merge inside its hook. A second hook invocation before
    # release would prove a second merge entered the protected region.
    hook.write_text(
        "#!/bin/sh\n"
        f"echo entered >> {shlex.quote(str(calls))}\n"
        f"touch {shlex.quote(str(entered))}\n"
        "count=0\n"
        f"while [ ! -f {shlex.quote(str(release))} ]; do\n"
        "  count=$((count + 1))\n"
        "  [ $count -lt 500 ] || exit 1\n"
        "  sleep 0.02\n"
        "done\n"
    )
    hook.chmod(0o755)
    started = threading.Event()

    def second_merge() -> str:
        started.set()
        return other.merge_into_base("T-02").status

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.merge_into_base, "T-01")
        try:
            wait_for_file(entered)
            second = pool.submit(second_merge)
            assert started.wait(5)
            # Both managers use the actual mutex. The active owner's PID remains
            # live even when the contender is another thread in that same PID.
            with (
                pytest.raises(LockTimeoutError),
                DirectoryMutex(manager.mutex.path, timeout=0.1).acquire(),
            ):
                pytest.fail("First merge must still own the lock")
            assert not second.done()
            assert calls.read_text().splitlines() == ["entered"]
        finally:
            release.touch()
        assert first.result(timeout=10).status == "merged"
        assert second.result(timeout=10) == "merged"
    assert calls.read_text().splitlines() == ["entered", "entered"]
    assert (manager.repo / "first.txt").read_text() == "first\n"
    assert (manager.repo / "second.txt").read_text() == "second\n"
    assert_clean(manager.repo)
    assert not manager.mutex.path.exists()


@pytest.mark.parametrize("ticket", ["../escape", "-option", "foo/bar", "bad..name", "main"])
def test_invalid_ticket_cannot_escape_worktree_root(manager: WorktreeManager, ticket: str) -> None:
    with pytest.raises(WorktreeError, match="branch"):
        manager.create(ticket)


def test_wrong_base_is_rejected(manager: WorktreeManager) -> None:
    with pytest.raises(WorktreeError, match="differs from configured base"):
        manager.create("T-01", "develop")
    git(manager.repo, "switch", "-c", "other")
    manager.create("T-01")
    with pytest.raises(WorktreeError, match="Expected main"):
        manager.merge_into_base("T-01")
    assert git(manager.repo, "branch", "--show-current") == "other"


@pytest.mark.parametrize("dirty_target", ["base", "lane"])
def test_dirty_checkout_is_preserved(manager: WorktreeManager, dirty_target: str) -> None:
    path = change(manager, "T-01", "lane.txt", "new\n")
    target = manager.repo if dirty_target == "base" else path
    (target / "untracked.txt").write_text("valuable work\n")
    with pytest.raises(WorktreeError, match="must be clean"):
        manager.merge_into_base("T-01")
    assert (target / "untracked.txt").read_text() == "valuable work\n"
    assert not manager.mutex.path.exists()


def test_dirty_worktree_is_not_force_removed(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    (path / "shared.txt").write_text("uncommitted\n")
    with pytest.raises(GitError):
        manager.remove("T-01")
    assert (path / "shared.txt").read_text() == "uncommitted\n"


def test_branch_checked_out_elsewhere_is_not_taken(
    manager: WorktreeManager, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    git(manager.repo, "worktree", "add", "-b", "T-01", str(elsewhere))
    with pytest.raises(WorktreeError, match="already checked out elsewhere"):
        manager.create("T-01")
    assert elsewhere.exists()


def test_unregistered_directory_is_not_removed(manager: WorktreeManager) -> None:
    path = manager.path_for("T-01")
    path.mkdir(parents=True)
    (path / "valuable.txt").write_text("keep\n")
    with pytest.raises(WorktreeError, match="unregistered"):
        manager.remove("T-01")
    assert (path / "valuable.txt").read_text() == "keep\n"


def test_switched_lane_is_rejected(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    git(path, "switch", "-c", "other")
    with pytest.raises(WorktreeError, match="cannot be reused"):
        manager.create("T-01")
    with pytest.raises(WorktreeError, match="registered worktree"):
        manager.merge_into_base("T-01")
    with pytest.raises(WorktreeError, match="not on ticket branch"):
        manager.remove("T-01")
    with pytest.raises(WorktreeError, match="managed ticket worktree"):
        manager.commit_all(path, "Wrong branch")


def test_commit_rejects_base_and_outside_directory(
    manager: WorktreeManager, tmp_path: Path
) -> None:
    for path in (manager.repo, tmp_path):
        with pytest.raises(WorktreeError, match="managed ticket worktree"):
            manager.commit_all(path, "Wrong checkout")


def test_symlink_ticket_path_is_rejected(manager: WorktreeManager, tmp_path: Path) -> None:
    manager.worktree_root.mkdir()
    (manager.worktree_root / "T-01").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(WorktreeError, match="symlink"):
        manager.create("T-01")


def test_non_conflict_merge_failure_is_typed_and_lock_released(manager: WorktreeManager) -> None:
    change(manager, "T-01", "lane.txt", "lane\n")
    hook = manager.repo / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(GitError) as error:
        manager.merge_into_base("T-01")
    assert error.value.exit_code != 0
    assert error.value.cwd == manager.repo
    assert_clean(manager.repo)
    assert not manager.mutex.path.exists()


def test_missing_base_is_typed_error(manager: WorktreeManager) -> None:
    other = WorktreeManager(manager.repo, base="missing")
    with pytest.raises(GitError):
        other.create("T-01")
    assert not other.mutex.path.exists()


def test_lock_timeout_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        DirectoryMutex(tmp_path / "mutex", timeout=-1)


def test_configured_base_and_custom_root(manager: WorktreeManager, tmp_path: Path) -> None:
    git(manager.repo, "switch", "-c", "release/current")
    custom = WorktreeManager(
        manager.repo, base="release/current", worktree_root=tmp_path / "custom lanes"
    )
    path = custom.create("T-01", "release/current")
    assert path == tmp_path / "custom lanes" / "T-01"
    (path / "lane.txt").write_text("new\n")
    assert custom.commit_all(path, "Add lane content")
    assert custom.sync_base_into("T-01").status == "merged"
    assert custom.merge_into_base("T-01").status == "merged"
    assert (manager.repo / "lane.txt").read_text() == "new\n"


@pytest.mark.parametrize("base", ["", "-option", "bad..name", "bad name"])
def test_invalid_base_name_is_rejected(manager: WorktreeManager, base: str) -> None:
    with pytest.raises(WorktreeError, match="Invalid base branch"):
        WorktreeManager(manager.repo, base=base)


def test_matching_tag_names_do_not_change_branch_selection(manager: WorktreeManager) -> None:
    git(manager.repo, "tag", "main")
    git(manager.repo, "tag", "T-01")
    path = change(manager, "T-01", "lane.txt", "correct branch\n")
    assert manager.sync_base_into("T-01").status == "merged"
    assert manager.merge_into_base("T-01").status == "merged"
    assert (manager.repo / "lane.txt").read_text() == "correct branch\n"
    head = git(path, "rev-parse", "HEAD")
    manager.remove("T-01")
    assert manager.create("T-01") == path
    assert git(path, "symbolic-ref", "HEAD") == "refs/heads/T-01"
    assert git(path, "rev-parse", "HEAD") == head


def test_foreign_repository_at_lane_path_is_not_touched(manager: WorktreeManager) -> None:
    path = manager.path_for("T-01")
    path.mkdir(parents=True)
    git(path, "init", "-b", "T-01")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "valuable.txt").write_text("unrelated repository\n")
    git(path, "add", "--all")
    git(path, "commit", "-m", "Unrelated content")
    head = git(path, "rev-parse", "HEAD")
    with pytest.raises(WorktreeError, match="registered worktree"):
        manager.sync_base_into("T-01")
    with pytest.raises(WorktreeError, match="registered worktree"):
        manager.merge_into_base("T-01")
    assert git(path, "rev-parse", "HEAD") == head
    assert_clean(path)


def test_merge_overrides_no_commit_preference(manager: WorktreeManager) -> None:
    change(manager, "T-01", "lane.txt", "new\n")
    git(manager.repo, "config", "branch.main.mergeOptions", "--no-commit")
    assert manager.merge_into_base("T-01").status == "merged"
    assert (manager.repo / "lane.txt").read_text() == "new\n"
    assert_clean(manager.repo)


def test_sync_can_merge_divergence_despite_ff_only_preference(manager: WorktreeManager) -> None:
    path = change(manager, "T-01", "lane.txt", "lane\n")
    (manager.repo / "base.txt").write_text("base\n")
    commit_all(manager.repo, "Advance base")
    git(manager.repo, "config", "merge.ff", "only")
    assert manager.sync_base_into("T-01").status == "merged"
    assert (path / "base.txt").read_text() == "base\n"
    assert_clean(path)
    assert manager.merge_into_base("T-01").status == "merged"
    assert_clean(manager.repo)


def test_all_checkouts_share_common_lock(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    other = WorktreeManager(path, base="T-01")
    assert manager.mutex.path == other.mutex.path


def test_stale_lock_reclamation_is_safe_under_contention(tmp_path: Path) -> None:
    lock = DirectoryMutex(tmp_path / "mutex")
    lock.path.mkdir()
    (lock.path / "owner.json").write_text(json.dumps({"pid": dead_pid(), "token": "old"}))
    start = threading.Barrier(3, timeout=5)
    holders: list[int] = []

    def acquire_once(number: int) -> None:
        start.wait()
        with DirectoryMutex(lock.path).acquire():
            holders.append(number)
            assert holders == [number]
            time.sleep(0.05)
            assert holders.pop() == number

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(acquire_once, 1)
        second = pool.submit(acquire_once, 2)
        start.wait()
        first.result(timeout=5)
        second.result(timeout=5)
    assert not lock.path.exists()


def test_stale_registration_requires_removal_before_reuse(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    shutil.rmtree(path)
    with pytest.raises(WorktreeError, match="cannot be reused"):
        manager.create("T-01")
    manager.remove("T-01")
    assert manager.create("T-01").exists()


def test_lock_owned_by_an_unsignalable_process_is_not_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock whose owner we may not signal is held, not stolen.

    On a shared machine the owning pid can belong to another user, so os.kill
    raises PermissionError. That means the process is alive and the lock is live;
    treating it as stale would let two lanes merge at once.
    """
    mutex = worktree.DirectoryMutex(tmp_path / "merge.lock", timeout=0.1)
    mutex.path.mkdir()
    (mutex.path / "owner.json").write_text(json.dumps({"pid": 4242, "token": "other"}))

    def deny(pid: int, sig: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr("cc_tandem.worktree.os.kill", deny)
    with pytest.raises(worktree.LockTimeoutError), mutex.acquire():
        pass  # pragma: no cover - the lock must not be granted
    assert (mutex.path / "owner.json").exists()


def test_owned_worktree_survives_removal_and_override_is_explicit(
    manager: WorktreeManager,
) -> None:
    path = manager.create("T-01")
    lock = manager.ownership("T-01")
    with lock.acquire("live-job"):
        with pytest.raises(WorktreeError, match="live-job"):
            manager.remove("T-01")
        assert path.exists()
        (path / "shared.txt").write_text("uncommitted")
        manager.remove("T-01", override=True)
        assert not path.exists()
        assert not lock.path.exists()


def test_removal_reclaims_stale_ownership(manager: WorktreeManager) -> None:
    path = manager.create("T-01")
    lock = manager.ownership("T-01")
    lock.path.parent.mkdir()
    (lock.path).write_text(
        json.dumps({"pid": dead_pid(), "job_id": "dead-job", "started_at": "then", "token": "old"})
    )
    manager.remove("T-01")
    assert not path.exists()
    assert not lock.path.exists()
