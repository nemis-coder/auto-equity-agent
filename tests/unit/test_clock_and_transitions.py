import time
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.clock import FakeClock, FixedAdvancingClock, build_clock
from app.domain.states import TERMINAL_STATES, WorkflowState
from app.domain.transitions import ALLOWED, TransitionError, check_transition

FIXED = datetime(2026, 9, 24, 12, tzinfo=UTC)


def test_fixed_clock_starts_at_fixed_now_and_advances():
    clock = FixedAdvancingClock(FIXED)
    first = clock.business_now()
    assert timedelta(0) <= first - FIXED < timedelta(seconds=1)
    time.sleep(0.05)
    assert clock.business_now() > first


def test_fixed_clock_requires_timezone_and_value():
    with pytest.raises(ValueError):
        FixedAdvancingClock(datetime(2026, 9, 24, 12))
    with pytest.raises(ValueError):
        build_clock("fixed", None)


def test_fake_clock_only_moves_when_told():
    clock = FakeClock(FIXED)
    assert clock.business_now() == FIXED
    clock.advance(minutes=16)
    assert clock.business_now() == FIXED + timedelta(minutes=16)


def test_terminal_states_have_no_exit():
    for state in TERMINAL_STATES:
        assert ALLOWED[state] == frozenset()


def test_human_review_never_goes_directly_to_ready():
    with pytest.raises(TransitionError):
        check_transition(WorkflowState.HUMAN_REVIEW, WorkflowState.READY_FOR_FINANCIAL)


def test_ready_only_from_document_validation():
    sources = {s for s, targets in ALLOWED.items() if WorkflowState.READY_FOR_FINANCIAL in targets}
    assert sources == {WorkflowState.DOCUMENT_VALIDATION}


def test_rejection_only_from_vehicle_eligibility():
    sources = {s for s, targets in ALLOWED.items() if WorkflowState.REJECTED in targets}
    assert sources == {WorkflowState.VEHICLE_ELIGIBILITY}
