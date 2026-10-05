"""Pure effort selection and bounded retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 60
DEFAULT_BACKOFF_CAP_SECONDS = 3600
# At the backoff cap this is roughly a day of waiting before a lane gives up.
DEFAULT_MAX_CAPACITY_WAITS = 24


class Effort(StrEnum):
    """Supported reasoning effort levels, ordered from lowest to highest."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def parse(cls, value: str) -> Effort:
        """Parse a supported level with a specific explanation for xhigh."""
        if value == "xhigh":
            raise ValueError("xhigh costs far more quota for little gain; use high instead")
        return cls(value)


class FailureClass(StrEnum):
    """Results that determine whether another attempt is useful."""

    OK = "ok"
    CAPACITY = "capacity"
    INFRASTRUCTURE = "infrastructure"
    IMPLEMENTATION = "implementation"
    STALLED = "stalled"
    OWNER_DECISION = "owner_decision"


@dataclass(frozen=True)
class Decision:
    """A retry instruction; stopped decisions retain effort and have no delay."""

    retry: bool
    effort: Effort
    attempt: int
    implementation_failures: int = 0
    backoff_seconds: int = 0
    backoff_count: int = 0


def escalate(effort: Effort) -> Effort:
    """Increase effort by one level, capped at high."""
    levels = list(Effort)
    return levels[min(levels.index(effort) + 1, len(levels) - 1)]


def for_difficulty(difficulty: str) -> Effort:
    """Map a ticket's difficulty to its initial effort."""
    levels = {
        "mechanical": Effort.LOW,
        "feature": Effort.MEDIUM,
        "architectural": Effort.HIGH,
    }
    try:
        return levels[difficulty]
    except KeyError:
        raise ValueError(f"Unknown difficulty: {difficulty!r}") from None


def next_attempt(
    outcome: FailureClass | str,
    effort: Effort,
    attempt: int,
    *,
    implementation_failures: int = 0,
    backoff_count: int = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_capacity_waits: int = DEFAULT_MAX_CAPACITY_WAITS,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    backoff_cap_seconds: int = DEFAULT_BACKOFF_CAP_SECONDS,
) -> Decision:
    """Apply a result to counters from *before* this invocation (initially zero).

    Capacity preserves the attempt budget, even at its limit. Every other result
    consumes an attempt, including success and owner decisions. Track prior
    implementation failures independently of attempts: infrastructure and capacity
    must not cause the first implementation failure to escalate. Pass the returned
    counters to the next call. Backoff count tracks prior consecutive waits, so
    quota delays can grow even when the attempt count does not. A lane still gives
    up after max_capacity_waits consecutive quota waits, so an exhausted quota
    cannot retry indefinitely.
    """
    if attempt < 0 or max_attempts < 1:
        raise ValueError("attempt must be non-negative and max_attempts must be at least 1")
    if implementation_failures < 0 or backoff_count < 0:
        raise ValueError("implementation_failures and backoff_count must be non-negative")
    if backoff_seconds < 1 or backoff_cap_seconds < 1:
        raise ValueError("backoff seconds and cap must be positive")
    if max_capacity_waits < 1:
        raise ValueError("max_capacity_waits must be at least 1")
    result = FailureClass(outcome)
    counted = attempt + (result != FailureClass.CAPACITY)
    failures = implementation_failures + (result == FailureClass.IMPLEMENTATION)
    waits = backoff_count + (result == FailureClass.CAPACITY)
    # Capacity is exempt from the attempt budget but not from giving up entirely:
    # an exhausted quota that never reopens would otherwise retry forever.
    exhausted = (
        counted >= max_attempts if result != FailureClass.CAPACITY else waits > max_capacity_waits
    )
    if result in (FailureClass.OK, FailureClass.OWNER_DECISION) or exhausted:
        return Decision(False, effort, counted, failures, 0, waits)
    delay = min(backoff_seconds, backoff_cap_seconds)
    # Stop doubling at the cap to keep even very large wait counters bounded.
    for _ in range(backoff_count):
        if delay == backoff_cap_seconds:
            break
        delay = min(delay * 2, backoff_cap_seconds)
    if result == FailureClass.STALLED or (
        result == FailureClass.IMPLEMENTATION and implementation_failures > 0
    ):
        effort = escalate(effort)
    return Decision(True, effort, counted, failures, delay, waits)
