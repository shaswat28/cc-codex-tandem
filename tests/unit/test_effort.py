"""Effort ladder transitions and retry invariants."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cc_tandem.effort import Decision, Effort, FailureClass, escalate, for_difficulty, next_attempt


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
@pytest.mark.parametrize(
    ("outcome", "retry", "higher", "count"),
    [
        (FailureClass.OK, False, False, 1),
        (FailureClass.CAPACITY, True, False, 0),
        (FailureClass.INFRASTRUCTURE, True, False, 1),
        (FailureClass.IMPLEMENTATION, True, False, 1),
        (FailureClass.STALLED, True, True, 1),
        (FailureClass.OWNER_DECISION, False, False, 1),
    ],
)
def test_transitions(
    outcome: FailureClass, retry: bool, higher: bool, count: int, effort: Effort
) -> None:
    decision = next_attempt(outcome.value, effort, 0)
    assert decision == Decision(
        retry,
        escalate(effort) if higher else effort,
        count,
        int(outcome == FailureClass.IMPLEMENTATION),
        60 if retry else 0,
        int(outcome == FailureClass.CAPACITY),
    )


@pytest.mark.parametrize("outcome", list(FailureClass))
@pytest.mark.parametrize("attempt", [2, 3, 100])
def test_exhaustion(outcome: FailureClass, attempt: int) -> None:
    decision = next_attempt(outcome, Effort.LOW, attempt)
    assert decision.retry == (outcome == FailureClass.CAPACITY)
    assert decision.effort == Effort.LOW
    assert decision.attempt == attempt + (outcome != FailureClass.CAPACITY)


@pytest.mark.parametrize("effort", list(Effort))
@pytest.mark.parametrize("attempt", [0, 1, 2, 3, 10000])
def test_owner_decision_never_escalates(effort: Effort, attempt: int) -> None:
    decision = next_attempt(
        FailureClass.OWNER_DECISION, effort, attempt, implementation_failures=10
    )
    assert decision == Decision(False, effort, attempt + 1, 10)


@pytest.mark.parametrize("outcome", list(FailureClass))
def test_attempt_accounting(outcome: FailureClass) -> None:
    assert next_attempt(outcome, Effort.LOW, 1).attempt == (
        1 if outcome == FailureClass.CAPACITY else 2
    )


def test_implementation_escalates_on_second_occurrence() -> None:
    first = next_attempt(FailureClass.IMPLEMENTATION, Effort.LOW, 0)
    assert first == Decision(True, Effort.LOW, 1, 1, 60)
    capacity = next_attempt(
        FailureClass.CAPACITY,
        first.effort,
        first.attempt,
        implementation_failures=first.implementation_failures,
    )
    infrastructure = next_attempt(
        FailureClass.INFRASTRUCTURE,
        capacity.effort,
        capacity.attempt,
        implementation_failures=capacity.implementation_failures,
        max_attempts=10,
    )
    second = next_attempt(
        FailureClass.IMPLEMENTATION,
        infrastructure.effort,
        infrastructure.attempt,
        implementation_failures=infrastructure.implementation_failures,
        max_attempts=10,
    )
    assert second == Decision(True, Effort.MEDIUM, 3, 2, 60)


def test_prior_other_failures_do_not_escalate_first_implementation() -> None:
    assert (
        next_attempt(FailureClass.IMPLEMENTATION, Effort.LOW, 5, max_attempts=10).effort
        == Effort.LOW
    )


def test_capacity_cannot_exhaust_attempt_budget() -> None:
    attempt = 0
    for wait in range(100):
        decision = next_attempt(
            FailureClass.CAPACITY,
            Effort.LOW,
            attempt,
            backoff_count=wait,
            max_capacity_waits=1000,
        )
        assert decision.retry and decision.effort == Effort.LOW
        attempt = decision.attempt
    assert attempt == 0
    assert next_attempt(FailureClass.IMPLEMENTATION, Effort.LOW, attempt).retry


def test_backoff_growth_and_cap() -> None:
    delays = [
        next_attempt(FailureClass.CAPACITY, Effort.LOW, 0, backoff_count=wait).backoff_seconds
        for wait in range(9)
    ]
    assert delays == [60, 120, 240, 480, 960, 1920, 3600, 3600, 3600]
    assert (
        next_attempt(
            FailureClass.INFRASTRUCTURE, Effort.LOW, 0, backoff_count=1000000
        ).backoff_seconds
        == 3600
    )


def test_custom_policy() -> None:
    assert next_attempt(
        FailureClass.CAPACITY,
        Effort.HIGH,
        0,
        max_attempts=1,
        backoff_seconds=10,
        backoff_cap_seconds=5,
    ) == Decision(True, Effort.HIGH, 0, 0, 5, 1)
    assert not next_attempt(FailureClass.IMPLEMENTATION, Effort.LOW, 0, max_attempts=1).retry


@pytest.mark.parametrize(
    ("attempt", "max_attempts", "backoff", "cap", "failures", "waits"),
    [
        (-1, 3, 60, 3600, 0, 0),
        (0, 0, 60, 3600, 0, 0),
        (0, 3, 0, 3600, 0, 0),
        (0, 3, 60, 0, 0, 0),
        (0, 3, 60, 3600, -1, 0),
        (0, 3, 60, 3600, 0, -1),
    ],
)
def test_invalid_policy(
    attempt: int, max_attempts: int, backoff: int, cap: int, failures: int, waits: int
) -> None:
    with pytest.raises(ValueError):
        next_attempt(
            FailureClass.STALLED,
            Effort.LOW,
            attempt,
            max_attempts=max_attempts,
            backoff_seconds=backoff,
            backoff_cap_seconds=cap,
            implementation_failures=failures,
            backoff_count=waits,
        )


def test_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        next_attempt("unknown", Effort.LOW, 0)


@given(
    outcome=st.sampled_from(list(FailureClass)),
    effort=st.sampled_from(list(Effort)),
    attempt=st.integers(min_value=0, max_value=10000),
    max_attempts=st.integers(min_value=1, max_value=10001),
    failures=st.integers(min_value=0, max_value=10000),
)
def test_effort_never_decreases(
    outcome: FailureClass, effort: Effort, attempt: int, max_attempts: int, failures: int
) -> None:
    decision = next_attempt(
        outcome, effort, attempt, max_attempts=max_attempts, implementation_failures=failures
    )
    assert list(Effort).index(decision.effort) >= list(Effort).index(effort)


def test_class_values_are_lowercase() -> None:
    for outcome in FailureClass:
        assert outcome.value == outcome.value.lower()
        assert FailureClass(outcome.value) is outcome


def test_capacity_eventually_gives_up() -> None:
    """An exhausted quota that never reopens must not retry forever."""
    decision = next_attempt(
        FailureClass.CAPACITY, Effort.MEDIUM, 0, backoff_count=24, max_capacity_waits=24
    )
    assert decision.retry is False
    assert decision.backoff_count == 25


def test_capacity_still_retries_below_the_wait_limit() -> None:
    decision = next_attempt(
        FailureClass.CAPACITY, Effort.MEDIUM, 0, backoff_count=23, max_capacity_waits=24
    )
    assert decision.retry is True
    assert decision.effort is Effort.MEDIUM
    assert decision.backoff_count == 24


def test_capacity_never_consumes_an_attempt_even_at_the_attempt_limit() -> None:
    decision = next_attempt(FailureClass.CAPACITY, Effort.LOW, 99, max_attempts=3)
    assert decision.retry is True
    assert decision.attempt == 99


def test_backoff_count_is_returned_for_the_next_call() -> None:
    """Every counter the function consumes is also one it returns."""
    decision = next_attempt(FailureClass.CAPACITY, Effort.LOW, 0)
    assert decision.backoff_count == 1
    again = next_attempt(
        FailureClass.CAPACITY,
        decision.effort,
        decision.attempt,
        backoff_count=decision.backoff_count,
    )
    assert again.backoff_count == 2
    assert again.backoff_seconds > decision.backoff_seconds


def test_a_non_capacity_result_does_not_increment_the_wait_counter() -> None:
    decision = next_attempt(FailureClass.IMPLEMENTATION, Effort.LOW, 0, backoff_count=4)
    assert decision.backoff_count == 4


def test_max_capacity_waits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_capacity_waits"):
        next_attempt(FailureClass.CAPACITY, Effort.LOW, 0, max_capacity_waits=0)
