"""Gate final (TDD §6.7): cada condición que falla bloquea READY y apunta a su recuperación."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain import readiness as R
from app.providers.offers import amortize

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=5)
RATE = Decimal("0.240000")


def _offer(key_cost=Decimal("0.00"), quote_id=None, **over) -> R.OfferEvidence:
    # Calendario tal como lo entrega el cotizador (mock); el gate no lo recalcula.
    schedule = amortize(Decimal("50000.00") + key_cost, RATE, 24)
    total = sum((Decimal(r["payment"]) for r in schedule), Decimal("0.00"))
    base = R.OfferEvidence(
        id="offer-1",
        status="ACTIVE",
        expires_at=LATER,
        profile_id="profile-1",
        quote_id=quote_id,
        cash_amount=Decimal("50000.00"),
        key_cost=key_cost,
        financed_principal=Decimal("50000.00") + key_cost,
        annual_nominal_rate=RATE,
        term_months=24,
        regular_payment=Decimal(schedule[0]["payment"]),
        last_payment=Decimal(schedule[-1]["payment"]),
        total_payment=total,
        schedule=tuple(schedule),
        display_hash="h" * 64,
        computed_display_hash="h" * 64,
        policy_version="demo_policy_v1",
    )
    return replace(base, **over)


def _docs():
    return tuple(
        R.DocumentEvidence(f"d{i}", slot, slot, f"sha{i}", f"e{i}", f"sha{i}", f"x{i}")
        for i, slot in enumerate(("IDENTITY", "INCOME", "VEHICLE_OWNERSHIP"))
    )


def evidence(**over) -> R.Evidence:
    base = R.Evidence(
        state="DOCUMENT_VALIDATION",
        input_revision=3,
        policy_version="demo_policy_v1",
        profile_complete=True,
        pending_proposal=False,
        owned_by_customer=True,
        blocking_debt=False,
        has_second_key=True,
        eligibility={"status": "ELIGIBLE", "key_quote_required": False},
        bureau_fingerprint="bfp",
        vehicle_fingerprint="vfp",
        payment_capacity=Decimal("9000.00"),
        profile=R.ProfileEvidence(
            "profile-1", "OK", LATER, "demo_policy_v1", "bfp", RATE, Decimal("100000.00")
        ),
        quote=None,
        offer=_offer(),
        selection=R.SelectionEvidence("offer-1", "h" * 64, NOW),
        last_invalidation_at=None,
        documents=_docs(),
        rules_now=tuple((f"R{i}", "PASS") for i in range(11)),
        validation_fingerprint="vf",
        current_validation_fingerprint="vf",
        open_review=False,
        open_material_operations=0,
        advisor_request_open=False,
    )
    return replace(base, **over)


def test_all_conditions_pass():
    verdict = R.readiness(evidence(), NOW)
    assert verdict.all_pass and verdict.recovery is None
    assert len(verdict.checks) == 14
    assert verdict.safe_evidence()["failed"] == []


def test_gate_rejects_a_tampered_middle_payment_even_when_total_is_unchanged():
    offer = _offer()
    schedule = [dict(row) for row in offer.schedule]
    schedule[1]["payment"] = str(Decimal(schedule[1]["payment"]) + Decimal("1000"))
    schedule[2]["payment"] = str(Decimal(schedule[2]["payment"]) - Decimal("1000"))
    verdict = R.readiness(evidence(offer=replace(offer, schedule=tuple(schedule))), NOW)
    assert not verdict.all_pass
    assert any(check.code == "OFFER" for check in verdict.failed)


QUOTE = R.QuoteEvidence("quote-1", Decimal("3000.00"), LATER, "VALID", "vfp")


@pytest.mark.parametrize(
    ("over", "code", "recovery"),
    [
        ({"state": "SIMULATION"}, "STATE", R.NOT_APPLICABLE),
        ({"pending_proposal": True}, "NO_PENDING_PROPOSAL", R.WAIT_CUSTOMER),
        ({"owned_by_customer": None}, "ELIGIBILITY", "VEHICLE_ELIGIBILITY"),
        ({"has_second_key": False}, "ELIGIBILITY", "VEHICLE_ELIGIBILITY"),
        (
            {
                "profile": R.ProfileEvidence(
                    "profile-1", "OK", NOW, "demo_policy_v1", "bfp", RATE, None
                )
            },
            "CREDIT_PROFILE",
            "PROFILING",
        ),  # fmt: skip
        ({"bureau_fingerprint": "otro"}, "CREDIT_PROFILE", "PROFILING"),
        ({"offer": _offer(expires_at=NOW)}, "OFFER", "SIMULATION"),
        ({"offer": _offer(regular_payment=Decimal("1.00"))}, "OFFER", "SIMULATION"),
        ({"offer": _offer(computed_display_hash="x" * 64)}, "OFFER", "SIMULATION"),
        ({"payment_capacity": Decimal("100.00")}, "OFFER", "SIMULATION"),
        ({"offer": _offer(financed_principal=Decimal("49000.00"))}, "OFFER", "SIMULATION"),
        ({"offer": _offer(schedule=())}, "OFFER", "SIMULATION"),
        ({"selection": None}, "SELECTION", "SIMULATION"),
        ({"selection": R.SelectionEvidence("offer-1", "z" * 64, NOW)}, "SELECTION", "SIMULATION"),
        ({"last_invalidation_at": NOW + timedelta(seconds=1)}, "SELECTION", "SIMULATION"),
        ({"documents": _docs()[:2]}, "DOCUMENTS", "DOCUMENT_COLLECTION"),
        (
            {"documents": (replace(_docs()[0], extraction_sha256="viejo"), *_docs()[1:])},
            "DOCUMENTS",
            "DOCUMENT_COLLECTION",
        ),  # fmt: skip
        ({"rules_now": (("DATES", "FAIL"),)}, "DOCUMENT_RULES", R.REVALIDATE),
        ({"validation_fingerprint": "otra"}, "VALIDATION_FINGERPRINT", R.REVALIDATE),
        ({"open_review": True}, "NO_OPEN_REVIEW", R.IN_REVIEW),
        ({"open_material_operations": 1}, "NO_OPEN_OPERATIONS", R.WAIT_PROVIDER),
        ({"advisor_request_open": True}, "NO_OPEN_CORRECTION", R.WAIT_CUSTOMER),
    ],
)
def test_each_failed_condition_blocks_ready_with_recovery(over, code, recovery):
    verdict = R.readiness(evidence(**over), NOW)
    assert not verdict.all_pass
    assert code in {c.code for c in verdict.failed}
    assert verdict.recovery == recovery


def test_key_quote_must_be_current_and_match_offer_exactly():
    key = {"has_second_key": False,
           "eligibility": {"status": "ELIGIBLE", "key_quote_required": True}}  # fmt: skip
    ok = evidence(**key, quote=QUOTE, offer=_offer(Decimal("3000.00"), "quote-1"))
    assert R.readiness(ok, NOW).all_pass
    for bad in (
        {"quote": replace(QUOTE, expires_at=NOW)},  # cotización vencida (D24)
        {"quote": replace(QUOTE, request_fingerprint="otro-auto")},
        {"offer": _offer(Decimal("2999.99"), "quote-1")},  # costo distinto en la oferta
        {"offer": _offer(Decimal("0.00"), None)},  # llave faltante sin financiar
    ):
        verdict = R.readiness(replace(ok, **bad), NOW)
        assert "KEY_QUOTE" in {c.code for c in verdict.failed}
        assert verdict.recovery in ("PROFILING", "SIMULATION")
    # Con llave: costo cero y sin cotización aplicada.
    assert "KEY_QUOTE" in {
        c.code for c in R.readiness(evidence(offer=_offer(quote_id="q")), NOW).failed
    }


def test_earliest_stage_wins_and_waits_take_precedence():
    both = evidence(selection=None, profile=None)
    assert R.readiness(both, NOW).recovery == "PROFILING"
    review = evidence(selection=None, open_review=True)
    assert R.readiness(review, NOW).recovery == R.IN_REVIEW


def test_fingerprint_changes_with_any_material_input():
    base = R.readiness(evidence(), NOW).fingerprint
    assert R.readiness(evidence(input_revision=4), NOW).fingerprint != base
    assert R.readiness(evidence(offer=_offer(display_hash="y" * 64)), NOW).fingerprint != base
    assert R.readiness(evidence(current_validation_fingerprint="z"), NOW).fingerprint != base
