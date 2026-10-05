"""Atomic runner checkpoints, readable without contacting the companion.

The coordinator owns this store in the base checkout. Ticket artifacts and execution
logs belong to each lane's checkout; only checkpoints are published centrally.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import cast

from cc_tandem.effort import Effort


class StateError(ValueError):
    """A checkpoint is invalid or would escape its coordinator directory."""


class Phase(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    SUBMITTING = "submitting"
    RUNNING = "running"
    WAITING = "waiting"
    COMMITTING = "committing"
    SYNCING = "syncing"
    MERGING = "merging"
    LOGGING = "logging"
    SYNCING_LOG = "syncing_log"
    MERGING_LOG = "merging_log"
    CLEANUP = "cleanup"
    DONE = "done"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CONFLICT = "conflict"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TicketState:
    ticket: str
    lane: int
    effort: Effort
    created_at: str
    updated_at: str
    phase: Phase = Phase.PENDING
    attempts: int = 0
    implementation_failures: int = 0
    backoff_count: int = 0
    worktree: str | None = None
    job_id: str | None = None
    retry_at: str | None = None
    finished_at: str | None = None
    gate_failure: str | None = None
    logs: tuple[str, ...] = ()
    error: str | None = None


def _record(value: object) -> TicketState:
    if not isinstance(value, dict):
        raise StateError("Ticket state must be an object")
    data = cast(dict[str, object], value).copy()
    required = {"ticket", "lane", "effort", "created_at", "updated_at"}
    fields = TicketState.__dataclass_fields__
    if required - data.keys() or data.keys() - fields.keys():
        raise StateError("Unknown or missing ticket state fields")
    for name in ("ticket", "created_at", "updated_at"):
        if not isinstance(data[name], str) or not data[name]:
            raise StateError(f"Invalid {name}")
    for name in ("lane", "attempts", "implementation_failures", "backoff_count"):
        number = data.get(name, 0)
        if type(number) is not int or number < 0:
            raise StateError(f"Invalid {name}")
    for name in ("worktree", "job_id", "retry_at", "finished_at", "gate_failure", "error"):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise StateError(f"Invalid {name}")
    logs = data.get("logs", [])
    if not isinstance(logs, list) or any(not isinstance(item, str) for item in logs):
        raise StateError("Invalid logs")
    data["logs"] = tuple(logs)
    try:
        data["effort"] = Effort.parse(str(data["effort"]))
        data["phase"] = Phase(str(data.get("phase", Phase.PENDING)))
        for name in ("created_at", "updated_at", "retry_at", "finished_at"):
            if data.get(name) is not None:
                parsed = datetime.fromisoformat(str(data[name]))
                if parsed.tzinfo is None:
                    raise ValueError("timestamps must include a timezone")
        return TicketState(**data)  # type: ignore[arg-type]
    except ValueError as exc:
        raise StateError(f"Invalid ticket state: {exc}") from exc


class StateStore:
    """Merge checkpoint updates under a writer lock, then atomically replace JSON.

    Readers never take a lock. Cross-process writers reload inside the lock to avoid
    losing another lane's transition. A failed write leaves the old file intact.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = repo.resolve()
        self.path = self.repo / ".tandem" / "state.json"
        self._lock = RLock()

    def _contained(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.repo):
            raise StateError(f"State path escapes coordinator checkout: {path}")

    def read(self) -> dict[str, TicketState]:
        self._contained(self.path)
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise StateError(f"Cannot read {self.path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise StateError("Unsupported state format")
        tickets = raw.get("tickets")
        if not isinstance(tickets, dict):
            raise StateError("State tickets must be an object")
        result: dict[str, TicketState] = {}
        for key, value in tickets.items():
            record = _record(value)
            if key != record.ticket:
                raise StateError("State ticket key does not match its record")
            result[record.ticket] = record
        return result

    def put(self, record: TicketState) -> TicketState:
        record = replace(record, updated_at=timestamp())
        _record(json.loads(json.dumps(asdict(record))))
        with self._lock:
            self._contained(self.path)
            guard = self.path.with_suffix(".lock")
            self._contained(guard)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with guard.open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                records = self.read()
                records[record.ticket] = record
                data = {
                    "version": 1,
                    "tickets": {key: asdict(item) for key, item in records.items()},
                }
                pending: Path | None = None
                try:
                    with NamedTemporaryFile(
                        mode="w", encoding="utf-8", dir=self.path.parent, delete=False
                    ) as stream:
                        pending = Path(stream.name)
                        json.dump(data, stream, indent=2)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    pending.replace(self.path)
                finally:
                    if pending is not None:
                        pending.unlink(missing_ok=True)
        return record

    def write_log(self, name: str, content: str) -> str:
        """Publish runner output in the coordinator's ignored runtime directory."""
        if not name or Path(name).name != name:
            raise StateError("Log name must be a single path component")
        target = self.path.parent / "logs" / name
        self._contained(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)
