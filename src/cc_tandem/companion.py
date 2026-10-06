"""Locate the companion and expose its command results without hiding failures."""

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from cc_tandem.backlog import Status
from cc_tandem.effort import FailureClass

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
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
    process_error: bool = False


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
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        return CompanionResult(1, "", str(error), command=command, process_error=True)
    try:
        parsed = cast(JsonValue, json.loads(completed.stdout))
    except json.JSONDecodeError:
        parsed = None
    return CompanionResult(
        completed.returncode, completed.stdout, completed.stderr, parsed, command
    )


def final_output(result: CompanionResult) -> str | None:
    """Read only the task's recorded final message, never summaries or logs."""
    if not isinstance(result.json, dict):
        return None
    stored = result.json.get("storedJob")
    payload = stored.get("result") if isinstance(stored, dict) else result.json
    if not isinstance(payload, dict):
        return None
    output = payload.get("rawOutput")
    return output if isinstance(output, str) else None


def awaiting_question(result: CompanionResult) -> str | None:
    """Recognise an operator-directed request and preserve its full final message."""
    output = final_output(result)
    if output is None:
        return None
    # Require an operator-directed question or an explicit request for a choice.
    # A question mark in code, a URL, or an explanatory heading is not enough.
    question = re.search(
        r"(?:^|[\n.!]\s*)(?:can|could|would|will|should|do)\s+(?:you|I|we)\b[^?]*\?"
        r"|(?:^|[\n.!]\s*)(?:which|what|how)\b[^?]*\b(?:you|your|I|we)\b[^?]*\?"
        r"|\bplease\s+(?:choose|select|confirm|decide|tell me)\b",
        output,
        re.IGNORECASE,
    )
    return output if question else None


def classify(
    result: CompanionResult,
    *,
    has_diff: bool = False,
    turn_started: bool = True,
    gate_passed: bool | None = None,
    ticket_status: Status | None = None,
    gate_failure: str | None = None,
    previous_gate_failure: str | None = None,
) -> FailureClass:
    """Classify an ended job using evidence supplied by the lane, never prose intent.

    Ticket status comes from the backlog parser, not output mentioning DONE or
    BLOCKED. Gate failure identifiers must be stable observable check diagnostics;
    equal non-empty identifiers mean a repeat. Unknown gate results are not passes.
    A zero exit alone cannot prove completion: DONE plus no failing gate is needed.
    Dry runs are recorded no-ops. Missing turn evidence defaults conservatively to
    started, preventing an ordinary no-change job from being called infrastructure.
    """
    if ticket_status == Status.BLOCKED:
        return FailureClass.OWNER_DECISION
    if result.dry_run:
        return FailureClass.OK
    if result.exit_code != 0 and USAGE_LIMIT_PATTERN.search(f"{result.stdout}\n{result.stderr}"):
        return FailureClass.CAPACITY
    if ticket_status != Status.DONE and awaiting_question(result) is not None:
        return FailureClass.AWAITING_INPUT
    if has_diff and gate_passed is False:
        if gate_failure and gate_failure == previous_gate_failure:
            return FailureClass.STALLED
        return FailureClass.IMPLEMENTATION
    if not has_diff and (result.process_error or not turn_started):
        return FailureClass.INFRASTRUCTURE
    if result.exit_code == 0 and ticket_status == Status.DONE and gate_passed is not False:
        return FailureClass.OK
    return FailureClass.STALLED


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
