import itertools

import pytest

from app.domain.eligibility import EligibilityStatus, RejectionReason, evaluate_eligibility

T, F, N = True, False, None


def _expected(owned, debt, key):
    """Oráculo escrito a partir del challenge, independiente de la implementación."""
    reasons = []
    if owned is F:
        reasons.append("VEHICLE_NOT_OWNED")
    if debt is T:
        reasons.append("BLOCKING_DEBT")
    if reasons:
        return "REJECTED", reasons, [], False
    missing = [
        n
        for n, v in (("owned_by_customer", owned), ("blocking_debt", debt), ("has_second_key", key))
        if v is None
    ]
    if missing:
        return "PENDING", [], missing, False
    return "ELIGIBLE", [], [], key is F


@pytest.mark.parametrize(("owned", "debt", "key"), list(itertools.product([T, F, N], repeat=3)))
def test_full_three_valued_matrix(owned, debt, key):
    status, reasons, missing, quote = _expected(owned, debt, key)
    result = evaluate_eligibility(owned, debt, key)
    assert result.status.value == status
    assert [r.value for r in result.rejection_reasons] == reasons
    assert list(result.missing) == missing
    assert result.key_quote_required is quote


def test_missing_second_key_is_never_a_rejection():
    result = evaluate_eligibility(True, False, False)
    assert result.status is EligibilityStatus.ELIGIBLE
    assert result.key_quote_required is True


def test_unknown_is_not_treated_as_false():
    assert evaluate_eligibility(None, False, True).status is EligibilityStatus.PENDING
    assert evaluate_eligibility(True, None, True).status is EligibilityStatus.PENDING


def test_rejection_reasons_are_distinct():
    assert evaluate_eligibility(False, False, True).rejection_reasons == (
        RejectionReason.VEHICLE_NOT_OWNED,
    )
    assert evaluate_eligibility(True, True, True).rejection_reasons == (
        RejectionReason.BLOCKING_DEBT,
    )
