"""Locate the companion and expose its command results without hiding failures."""

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type Outcome = Literal["ok", "usage_limit", "failed"]
type VersionKey = tuple[int, int, int, bool, tuple[tuple[int, int | str], ...]]

SEARCH_PATTERN = ".claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs"
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
USAGE_LIMIT_PATTERN = re.compile(
    r"usage[\s_-]+limit|rate[\s_-]+limit|quota[\s_-]+(?:exceeded|exhausted)"
    r"|(?:reached|exceeded|hit)\s+(?:your\s+)?(?:usage\s+)?limit"
    r"|too many requests",
    re.IGNORECASE,
)


class CompanionNotFound(FileNotFoundError):  # noqa: N818 - ticket specifies this public name
    """No installed companion was found."""


class CompanionInvalid(ValueError):  # noqa: N818 - ticket specifies this public name
    """An explicit companion path or status response is invalid."""


@dataclass(frozen=True)
class CompanionResult:
    exit_code: int
    stdout: str
    stderr: str
    json: JsonValue = None
    command: tuple[str, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class StatusRecord:
    id: str
    status: str
    summary: str | None = None


@dataclass(frozen=True)
class CompanionStatus:
    running: list[StatusRecord]
    recent: list[StatusRecord]


def _version_key(version: str) -> VersionKey | None:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        return None
    major, minor, patch, prerelease = match.groups()
    identifiers: list[tuple[int, int | str]] = []
    if prerelease is not None:
        for identifier in prerelease.split("."):
            if identifier.isdigit():
                if len(identifier) > 1 and identifier.startswith("0"):
                    return None
                identifiers.append((0, int(identifier)))
            else:
                identifiers.append((1, identifier))
    return int(major), int(minor), int(patch), prerelease is None, tuple(identifiers)


def locate_companion() -> Path:
    """Resolve an explicit override or the highest installed semantic version."""
    override = os.environ.get("CC_TANDEM_COMPANION")
    if override is not None:
        path = Path(override).expanduser()
        if not path.is_file():
            raise CompanionInvalid(f"CC_TANDEM_COMPANION is not a file: {override}")
        return path
    home = Path.home()
    candidates: list[tuple[VersionKey, Path]] = []
    for path in home.glob(SEARCH_PATTERN):
        version = _version_key(path.parent.parent.name)
        if version is not None and path.is_file():
            candidates.append((version, path))
    if not candidates:
        raise CompanionNotFound(f"No companion found; searched {home / SEARCH_PATTERN}")
    return max(candidates, key=lambda candidate: (candidate[0], str(candidate[1])))[1]


def run(args: Sequence[str]) -> CompanionResult:
    """Invoke Node without a shell; non-zero exits remain inspectable results.

    Note: the companion treats an unrecognised argument as the task prompt, so a
    typo such as ``task --help`` starts a real Codex turn. Callers must pass only
    arguments the installed companion documents.
    """
    if os.environ.get("CC_TANDEM_DRY_RUN") == "1":
        command = ("node", os.environ.get("CC_TANDEM_COMPANION", SEARCH_PATTERN), *args)
        return CompanionResult(0, "", "", command=command, dry_run=True)
    command = ("node", str(locate_companion()), *args)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        parsed = cast(JsonValue, json.loads(completed.stdout))
    except json.JSONDecodeError:
        parsed = None
    return CompanionResult(
        completed.returncode, completed.stdout, completed.stderr, parsed, command
    )


def classify(result: CompanionResult) -> Outcome:
    """A successful exit is never a usage limit, however the output reads.

    The pattern matches phrases such as "rate limit" that a successful job may
    legitimately print (for example while implementing rate limiting). Checking
    the exit code first keeps that from being retried forever at the same effort.
    """
    if result.exit_code == 0:
        return "ok"
    if USAGE_LIMIT_PATTERN.search(f"{result.stdout}\n{result.stderr}"):
        return "usage_limit"
    return "failed"


def _status_records(value: JsonValue) -> list[StatusRecord]:
    if not isinstance(value, list):
        raise CompanionInvalid("Companion status lists must be arrays")
    records: list[StatusRecord] = []
    for item in value:
        if not isinstance(item, dict):
            raise CompanionInvalid("Companion status records must be objects")
        identifier, status, summary = item.get("id"), item.get("status"), item.get("summary")
        if not isinstance(identifier, str) or not isinstance(status, str):
            raise CompanionInvalid("Companion status records require string id and status")
        if summary is not None and not isinstance(summary, str):
            raise CompanionInvalid("Companion status summary must be a string or null")
        records.append(StatusRecord(identifier, status, summary))
    return records


def status_all() -> CompanionStatus:
    """Read the running and recent job lists, ignoring extra response fields."""
    result = run(["status", "--all", "--json"])
    if result.dry_run:
        return CompanionStatus([], [])
    if result.exit_code != 0:
        raise CompanionInvalid(f"Companion status failed ({result.exit_code}): {result.stderr}")
    if not isinstance(result.json, dict):
        raise CompanionInvalid("Companion status must be a JSON object")
    return CompanionStatus(
        _status_records(result.json.get("running", [])),
        _status_records(result.json.get("recent", [])),
    )
