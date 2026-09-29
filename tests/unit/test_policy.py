from decimal import Decimal as D

import pytest

from app.domain.policy import MalformedBureauResponse, ProfileOutcome, derive_profile, load_policy

POLICY = load_policy("demo_policy_v1")


def response(**overrides):
    base = {
        "status": "OK",
        "score": 720,
        "history_summary": {"open_trades": 2, "delinquencies_last_12m": 0},
        "conditions": {"currency": "MXN", "max_financed_principal": "100000.00"},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("score", "band", "rate", "cap"),
    [
        (850, "A", D("0.24"), D("100000.00")),
        (700, "A", D("0.24"), D("100000.00")),
        (699, "B", D("0.36"), D("50000.00")),
        (600, "B", D("0.36"), D("50000.00")),
    ],
)
def test_bands_at_boundaries(score, band, rate, cap):
    profile = derive_profile(POLICY, response(score=score))
    assert profile.outcome is ProfileOutcome.OK
    assert (profile.band, profile.annual_nominal_rate, profile.max_financed_principal) == (
        band,
        rate,
        cap,
    )


def test_effective_cap_is_minimum_of_provider_and_band():
    conditions = {"currency": "MXN", "max_financed_principal": "40000.00"}
    profile = derive_profile(POLICY, response(conditions=conditions))
    assert profile.max_financed_principal == D("40000.00")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"score": 599}, "SCORE_BELOW_POLICY"),
        ({"history_summary": {"delinquencies_last_12m": 1}}, "BUREAU_DELINQUENCIES"),
        ({"status": "REVIEW"}, "BUREAU_STATUS_REVIEW"),
    ],
)
def test_review_cases_never_invent_rejection(overrides, reason):
    profile = derive_profile(POLICY, response(**overrides))
    assert profile.outcome is ProfileOutcome.REVIEW
    assert profile.reason_code == reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"score": 900},
        {"score": 299},
        {"score": "720"},
        {"score": True},
        {"status": "APPROVED"},
        {"history_summary": None},
        {"history_summary": {"delinquencies_last_12m": -1}},
        {"conditions": {"currency": "USD", "max_financed_principal": "1"}},
        {"conditions": {"currency": "MXN", "max_financed_principal": "-5"}},
        {"conditions": {"currency": "MXN"}},
    ],
)
def test_malformed_response_is_provider_failure(overrides):
    with pytest.raises(MalformedBureauResponse):
        derive_profile(POLICY, response(**overrides))
