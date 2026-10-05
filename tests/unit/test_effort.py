"""Effort ladder transitions and retry invariants."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cc_tandem.effort import Decision, Effort, Outcome, escalate, for_difficulty, next_attempt


@pytest.mark.parametrize("effort", list(Effort))
def test_parse(effort: Effort) -> None:
    assert Effort.parse(effort.value) is effort


def test_xhigh_rejected() -> None:
    with pytest.raises(ValueError, match="far more quota for little gain"):
        Effort.parse("xhigh")


def test_unknown_effort() -> None:
    with pytest.raises(ValueError):
        Effort.parse("unknown")


@pytest.mark.parametrize(
    ("effort", "expected"),
    [(Effort.LOW, Effort.MEDIUM), (Effort.MEDIUM, Effort.HIGH), (Effort.HIGH, Effort.HIGH)],
)
def test_escalate(effort: Effort, expected: Effort) -> None:
    assert escalate(effort) is expected


@pytest.mark.parametrize(
    ("difficulty", "expected"),
    [("mechanical", Effort.LOW), ("feature", Effort.MEDIUM), ("architectural", Effort.HIGH)],
)
def test_difficulty(difficulty: str, expected: Effort) -> None:
    assert for_difficulty(difficulty) is expected


def test_unknown_difficulty() -> None:
    with pytest.raises(ValueError, match="Unknown difficulty"):
        for_difficulty("unknown")


@pytest.mark.parametrize("effort", list(Effort))
@pytest.mark.parametrize("outcome", list(Outcome))
def test_transitions(outcome: Outcome, effort: Effort) -> None:
    decision = next_attempt(outcome.value, effort, 1)
    if outcome == Outcome.USAGE_LIMIT:
        assert decision == Decision(True, effort, 60)
    elif outcome == Outcome.FAILED:
        # A real failure escalates effort and still backs off, so a job that keeps
        # failing is not retried immediately in a tight loop.
        assert decision == Decision(True, escalate(effort), 60)
    else:
        assert decision == Decision(False, effort)


@pytest.mark.parametrize("outcome", list(Outcome))
@pytest.mark.parametrize("attempt", [3, 4, 100])
def test_exhaustion(outcome: Outcome, attempt: int) -> None:
    assert next_attempt(outcome, Effort.LOW, attempt) == Decision(False, Effort.LOW)


def test_backoff_growth_and_cap() -> None:
    delays = [
        next_attempt(Outcome.USAGE_LIMIT, Effort.LOW, attempt, max_attempts=20).backoff_seconds
        for attempt in range(1, 10)
    ]
    assert delays == [60, 120, 240, 480, 960, 1920, 3600, 3600, 3600]


def test_custom_policy() -> None:
    assert next_attempt(
        Outcome.USAGE_LIMIT,
        Effort.HIGH,
        1,
        max_attempts=2,
        backoff_seconds=10,
        backoff_cap_seconds=5,
    ) == Decision(True, Effort.HIGH, 5)
    assert next_attempt(Outcome.FAILED, Effort.LOW, 1, max_attempts=1).retry is False


@pytest.mark.parametrize(
    ("attempt", "max_attempts", "backoff", "cap"),
    [(0, 3, 60, 3600), (-1, 3, 60, 3600), (1, 0, 60, 3600), (1, 3, 0, 3600), (1, 3, 60, 0)],
)
def test_invalid_policy(attempt: int, max_attempts: int, backoff: int, cap: int) -> None:
    with pytest.raises(ValueError):
        next_attempt(
            Outcome.FAILED,
            Effort.LOW,
            attempt,
            max_attempts=max_attempts,
            backoff_seconds=backoff,
            backoff_cap_seconds=cap,
        )


def test_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        next_attempt("unknown", Effort.LOW, 1)


@given(
    outcome=st.sampled_from(list(Outcome)),
    effort=st.sampled_from(list(Effort)),
    attempt=st.integers(min_value=1, max_value=10000),
    max_attempts=st.integers(min_value=1, max_value=10001),
)
def test_effort_never_decreases(
    outcome: Outcome,
    effort: Effort,
    attempt: int,
    max_attempts: int,
) -> None:
    decision = next_attempt(outcome, effort, attempt, max_attempts=max_attempts)
    assert list(Effort).index(decision.effort) >= list(Effort).index(effort)


def test_outcome_values_are_lowercase() -> None:
    """Every member parses from its lowercase spelling, blocked included."""
    for outcome in Outcome:
        assert outcome.value == outcome.value.lower()
        assert Outcome(outcome.value) is outcome


def test_failure_backoff_grows_with_attempts() -> None:
    first = next_attempt(Outcome.FAILED, Effort.LOW, 1)
    second = next_attempt(Outcome.FAILED, Effort.LOW, 2)
    assert second.backoff_seconds > first.backoff_seconds
