"""Pure effort selection and bounded retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 60
DEFAULT_BACKOFF_CAP_SECONDS = 3600


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


class Outcome(StrEnum):
    """Results that determine whether another attempt is useful."""

    OK = "ok"
    USAGE_LIMIT = "usage_limit"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Decision:
    """A retry instruction; stopped decisions retain effort and have no delay."""

    retry: bool
    effort: Effort
    backoff_seconds: int = 0


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
    outcome: Outcome | str,
    effort: Effort,
    attempt: int,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    backoff_cap_seconds: int = DEFAULT_BACKOFF_CAP_SECONDS,
) -> Decision:
    """Decide after a one-based completed attempt, without waiting or performing I/O.

    Usage limits consume an attempt but preserve effort. Only actual failures
    escalate. Every retry backs off: delay doubles with each completed attempt
    and never exceeds the cap, so a failing job cannot be hammered.
    """
    if attempt < 1 or max_attempts < 1:
        raise ValueError("attempt and max_attempts must be at least 1")
    if backoff_seconds < 1 or backoff_cap_seconds < 1:
        raise ValueError("backoff seconds and cap must be positive")
    result = Outcome(outcome)
    if result in (Outcome.OK, Outcome.BLOCKED) or attempt >= max_attempts:
        return Decision(retry=False, effort=effort)
    delay = min(backoff_seconds, backoff_cap_seconds)
    # Stop doubling at the cap to keep even very large attempt numbers bounded.
    for _ in range(attempt - 1):
        if delay == backoff_cap_seconds:
            break
        delay = min(delay * 2, backoff_cap_seconds)
    if result == Outcome.FAILED:
        return Decision(retry=True, effort=escalate(effort), backoff_seconds=delay)
    return Decision(retry=True, effort=effort, backoff_seconds=delay)
