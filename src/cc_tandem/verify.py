"""Repeat trusted project gates and inspect changes before accepting a lane.

Gate strings use the shell so project commands such as ``make check`` and pipelines
retain their meaning. Only run commands reviewed by the caller. Argument sequences
run without a shell. Neither verification nor scanning consults the companion.
"""

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from cc_tandem.worktree import GitError

Command = str | Sequence[str]
REVIEW_CHECKLIST = (
    "Re-run claimed checks and compare exit codes and output with the claims.",
    "Open evidence files and confirm they support the claims.",
    "Review intent: is this the right behaviour, including unspecified edge cases?",
    "Review conformance separately: does the behaviour match what was asked?",
    "Record ticket defects under the affected ticket in BACKLOG.md, not only in code.",
    "Review the diff, added lines, commit messages and branch names for defects and attribution.",
    "Run the gate independently at each commit before offering to push.",
)
DEFAULT_ALLOWLIST: tuple[str, ...] = ()
# Files that legitimately name the assistants because they describe this tooling.
# Attribution is still reported in them; only bare product mentions are allowed.
DEFAULT_PROJECT_DOCS = (
    "README*",
    "AGENTS.md",
    "CLAUDE.md",
    "BACKLOG.md",
    "docs/*.md",
    ".claude-plugin/*.json",
    "skills/*/SKILL.md",
)
_MENTION = re.compile(r"(?<![A-Za-z0-9])(?:claude|codex)(?![A-Za-z0-9])", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|git@)[^\s<>\"']+", re.IGNORECASE)
_ATTRIBUTION = re.compile(
    r"co[- ]authored|generated\s+(?:with|by)|(?:written|built|implemented|assisted)\s+by|"
    r"thanks\s+to|\b(?:author|assistant)[\"']?\s*:|"
    r"\b(?:claude|codex)\s+(?:wrote|built|implemented|generated|authored)\b",
    re.IGNORECASE,
)
_LOCK_NAMES = {
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}
_DEPENDENCY = re.compile(
    r"^\s*(?:name\s*=\s*(?P<name>\"[^\"]+\"|'[^']+')|"
    r"(?P<key>[^\s:]+)\s*:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CheckResult:
    command: str | tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class MentionFinding:
    source: str
    line: int
    text: str
    mention: str


@dataclass(frozen=True)
class CommitResult:
    commit: str
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


@dataclass(frozen=True)
class CommitVerification:
    commits: tuple[CommitResult, ...]

    @property
    def first_failure(self) -> CommitResult | None:
        return next((commit for commit in self.commits if not commit.passed), None)

    @property
    def passed(self) -> bool:
        return self.first_failure is None


def _commands(commands: Iterable[Command]) -> tuple[str | tuple[str, ...], ...]:
    if isinstance(commands, str):
        raise ValueError("Pass a collection of commands, not a single string")
    result: list[str | tuple[str, ...]] = []
    for command in commands:
        normalized = command if isinstance(command, str) else tuple(command)
        if not normalized or (isinstance(normalized, str) and not normalized.strip()):
            raise ValueError("Gate commands must not be empty")
        result.append(normalized)
    return tuple(result)


def _output(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def rerun_checks(
    commands: Iterable[Command], *, cwd: Path | None = None, timeout: float | None = 300
) -> tuple[CheckResult, ...]:
    """Run all gates in order, retaining failures, output and launch/timeout errors."""
    if timeout is not None and timeout <= 0:
        raise ValueError("Timeout must be positive")
    results: list[CheckResult] = []
    for command in _commands(commands):
        try:
            process = subprocess.run(
                command,
                shell=isinstance(command, str),
                cwd=cwd,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                CheckResult(command, None, _output(exc.stdout), _output(exc.stderr), True)
            )
        except OSError as exc:
            results.append(CheckResult(command, None, "", str(exc)))
        else:
            results.append(CheckResult(command, process.returncode, process.stdout, process.stderr))
    return tuple(results)


def scan_assistant_mentions(
    diff_or_paths: str | Path | Iterable[Path],
    *,
    commit_messages: Iterable[str] = (),
    branch_names: Iterable[str] = (),
    allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
    project_docs: Iterable[str] = DEFAULT_PROJECT_DOCS,
) -> tuple[MentionFinding, ...]:
    """Scan a unified diff, file contents, and explicitly supplied git metadata.

    Strings are diff text (or plain text); Path objects select files. Regex allowlist
    matches and URLs suppress only their own spans, never the rest of the line.
    Lock-file dependency declarations are legitimate; attribution remains flagged.
    Files matching project_docs describe this tooling, so bare product mentions in
    them are allowed; attribution in those files is still reported. The default set
    is generic, so the scanner works in any repository. No git commands are run by scanning.
    """
    patterns = tuple(re.compile(pattern) for pattern in allowlist)
    doc_globs = tuple(project_docs)
    findings: list[MentionFinding] = []

    def is_project_doc(source: str) -> bool:
        candidate = PurePosixPath(source.replace("\\", "/"))
        parts = candidate.parts
        return any(
            candidate.match(glob)
            or any(PurePosixPath(*parts[i:]).match(glob) for i in range(len(parts)))
            for glob in doc_globs
        )

    def scan(source: str, number: int, line: str, *, project_doc: bool = False) -> None:
        attribution = bool(_ATTRIBUTION.search(line))
        if not attribution and project_doc:
            return
        scrubbed = _URL.sub("", line)
        if not attribution and Path(source).name in _LOCK_NAMES:
            dependency = _DEPENDENCY.search(scrubbed)
            if dependency:
                start, end = dependency.span("name" if dependency["name"] else "key")
                scrubbed = scrubbed[:start] + scrubbed[end:]
        for pattern in patterns:
            scrubbed = pattern.sub("", scrubbed)
        for match in _MENTION.finditer(scrubbed):
            findings.append(MentionFinding(source, number, line, match[0]))

    if isinstance(diff_or_paths, str):
        lines = diff_or_paths.splitlines()
        is_diff = any(line.startswith(("diff --git ", "@@ ", "+++ ")) for line in lines)
        source, number, in_hunk = "<text>", 0, False
        for index, line in enumerate(lines, 1):
            if not is_diff:
                scan(source, index, line)
            elif line.startswith("+++ ") and not in_hunk:
                source = line[4:].removeprefix("b/").strip('"')
            elif line.startswith("diff --git "):
                in_hunk = False
            elif line.startswith("@@ "):
                match = re.search(r"\+(\d+)", line)
                if match:
                    number, in_hunk = int(match[1]), True
            elif in_hunk and line.startswith("+"):
                scan(source, number, line[1:], project_doc=is_project_doc(source))
                number += 1
            elif in_hunk and line.startswith(" "):
                number += 1
    else:
        paths = (diff_or_paths,) if isinstance(diff_or_paths, Path) else diff_or_paths
        for path in paths:
            content = path.read_text(encoding="utf-8")
            project_doc = is_project_doc(str(path))
            for number, line in enumerate(content.splitlines(), 1):
                scan(str(path), number, line, project_doc=project_doc)
    for index, message in enumerate(commit_messages, 1):
        for number, line in enumerate(message.splitlines(), 1):
            scan(f"commit-message[{index}]", number, line)
    for index, branch in enumerate(branch_names, 1):
        scan(f"branch[{index}]", 1, branch)
    return tuple(findings)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise GitError(repo, result)
    return result.stdout


def verify_each_commit(
    revision_range: str,
    commands: Iterable[Command],
    *,
    repo: Path = Path(),
    timeout: float | None = 300,
) -> CommitVerification:
    """Check commits oldest-first, stopping at the first failing gate.

    Accept a single git revision/range, not options. Each commit gets a fresh detached
    worktree, so build artifacts cannot hide a later failure. Only disposable files
    are force-removed; the caller's checkout, branch and dirty files are untouched.
    Invalid ranges and worktree lifecycle errors raise GitError, never a passing result.
    An empty range returns an empty report. Gates must be nonempty.
    """
    if not revision_range or revision_range.startswith("-"):
        raise ValueError("Expected a git revision range, not an option")
    gates = _commands(commands)
    if not gates:
        raise ValueError("Per-commit verification requires gate commands")
    if timeout is not None and timeout <= 0:
        raise ValueError("Timeout must be positive")
    repo = repo.resolve()
    commits = _git(repo, "rev-list", "--reverse", "--topo-order", revision_range, "--").splitlines()
    results: list[CommitResult] = []
    with TemporaryDirectory(prefix="tandem-verify-") as temporary:
        path = Path(temporary) / "checkout"
        for commit in commits:
            _git(repo, "worktree", "add", "--detach", str(path), commit)
            try:
                result = CommitResult(commit, rerun_checks(gates, cwd=path, timeout=timeout))
                results.append(result)
            finally:
                _git(repo, "worktree", "remove", "--force", str(path))
            if not result.passed:
                break
    return CommitVerification(tuple(results))
