"""Per-commit gates run only in disposable, isolated test repositories."""

import os
import sys
from pathlib import Path

import pytest
from tests.integration.test_worktree import git

from cc_tandem.verify import verify_each_commit
from cc_tandem.worktree import GitError

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    path = tmp_path / "temporary repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    commit(path, "base")
    return path


def commit(repo: Path, value: str) -> str:
    (repo / "value.txt").write_text(value)
    git(repo, "add", "--all")
    git(repo, "commit", "-m", f"Set value to {value}")
    return git(repo, "rev-parse", "HEAD")


def test_first_broken_commit_not_repaired_tip(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    good = commit(repo, "good")
    broken = commit(repo, "broken")
    tip = commit(repo, "repaired")
    (repo / "value.txt").write_text("dirty caller checkout")
    (repo / "untracked.txt").write_text("keep")
    registrations = git(repo, "worktree", "list", "--porcelain")
    result = verify_each_commit(
        f"{base}..{tip}",
        [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; "
                "print(Path.cwd()); Path('artifact.txt').write_text('new'); "
                "sys.exit(Path('value.txt').read_text() == 'broken')",
            ]
        ],
        repo=repo,
    )
    assert [entry.commit for entry in result.commits] == [good, broken]
    assert result.first_failure == result.commits[1]
    assert not result.passed
    assert result.commits[1].checks[0].returncode == 1
    assert git(repo, "rev-parse", "HEAD") == tip
    assert git(repo, "branch", "--show-current") == "main"
    assert (repo / "value.txt").read_text() == "dirty caller checkout"
    assert (repo / "untracked.txt").read_text() == "keep"
    assert git(repo, "worktree", "list", "--porcelain") == registrations
    assert not Path(result.commits[0].checks[0].stdout.strip()).exists()


def test_all_commits_pass_and_artifacts_do_not_leak(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    first = commit(repo, "first")
    second = commit(repo, "second")
    result = verify_each_commit(
        f"{base}..HEAD",
        [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert not Path('artifact').exists(); "
                "Path('artifact').touch()",
            ]
        ],
        repo=repo,
    )
    assert result.passed and result.first_failure is None
    assert [entry.commit for entry in result.commits] == [first, second]
    assert verify_each_commit("HEAD..HEAD", ["true"], repo=repo).commits == ()


def test_bad_range_is_an_error(repo: Path) -> None:
    with pytest.raises(GitError):
        verify_each_commit("missing..HEAD", ["true"], repo=repo)


@pytest.mark.parametrize("revision", ["", "--all"])
def test_option_ranges_rejected(repo: Path, revision: str) -> None:
    with pytest.raises(ValueError, match="revision range"):
        verify_each_commit(revision, ["true"], repo=repo)


def test_empty_gates_and_invalid_timeout_rejected(repo: Path) -> None:
    with pytest.raises(ValueError, match="gate commands"):
        verify_each_commit("HEAD", [], repo=repo)
    with pytest.raises(ValueError, match="Timeout"):
        verify_each_commit("HEAD", ["true"], repo=repo, timeout=0)


def test_cleanup_after_gate_raises(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registrations = git(repo, "worktree", "list", "--porcelain")

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("interrupted gate")

    monkeypatch.setattr("cc_tandem.verify.rerun_checks", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        verify_each_commit("HEAD", ["true"], repo=repo)
    assert git(repo, "worktree", "list", "--porcelain") == registrations
