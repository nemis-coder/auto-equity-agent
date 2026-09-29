"""Ofertas del cotizador externo simulado y su validación (decisión 0038).

El mock del cotizador decide montos, plazos y calendario; la app solo valida su respuesta.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.domain.simulation import InvalidOffers, offer_hash, parse_offers, schedule_consistent
from app.providers.offers import MockOfferProvider, amortize

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
EXPIRES = NOW + timedelta(days=7)

# Valores de referencia de TDD §6.5, escritos a mano (no se calculan con la función).
REFERENCE = [
    # (capital, cuota regular, último pago, total)
    (D("50000.00"), D("2643.55"), D("2643.72"), D("63445.37")),
    (D("53000.00"), D("2802.17"), D("2802.11"), D("67252.02")),
]


@pytest.mark.parametrize(("principal", "regular", "last", "total"), REFERENCE)
def test_mock_schedule_matches_reference_values(principal, regular, last, total):
    rows = amortize(principal, D("0.24"), 24)
    payments = [D(r["payment"]) for r in rows]
    assert len(rows) == 24 and set(payments[:-1]) == {regular}
    assert payments[-1] == last and sum(payments) == total
    assert D(rows[-1]["balance"]) == D("0.00")
    assert sum(D(r["principal"]) for r in rows) == principal


def _request(**over):
    base = {"annual_nominal_rate": "0.24", "max_financed_principal": "100000.00",
            "monthly_net_income": "20000.00", "key_cost": "3000.00", "currency": "MXN"}  # fmt: skip
    return {**base, **over}


def _mock_offers(**over):
    provider = MockOfferProvider.__new__(MockOfferProvider)  # sin ledger: solo la tarifa
    import json

    from app.providers.offers import TARIFF

    provider._tariff = json.loads(TARIFF.read_text(encoding="utf-8"))
    return provider._offers(_request(**over))


def test_mock_offers_amounts_within_cap_and_capacity_with_key_included_once():
    offers = _mock_offers()
    got = {(o["cash_amount"], o["term_months"]) for o in offers}
    # Tope 100k: 100k + 3k de llave no cabe. Capacidad 6k: 75k a 12 meses no cabe.
    assert got == {("25000.00", 12), ("25000.00", 24), ("50000.00", 12), ("50000.00", 24),
                   ("75000.00", 24)}  # fmt: skip
    for o in offers:
        assert D(o["financed_principal"]) == D(o["cash_amount"]) + D("3000.00")
        assert max(D(o["regular_payment"]), D(o["last_payment"])) <= D("6000.00")


def test_low_income_profile_can_get_no_offers():
    assert _mock_offers(monthly_net_income="3000.00") == []


def _parse(offers, **over):
    args = dict(key_cost=D("3000.00"), annual_rate=D("0.24"),
                max_financed_principal=D("100000.00"), payment_capacity=D("6000.00"),
                expires_at=EXPIRES)  # fmt: skip
    args.update(over)
    return parse_offers({"currency": "MXN", "offers": offers}, **args)


def test_valid_response_is_parsed_and_sorted_by_amount_then_term():
    parsed = _parse(_mock_offers())
    assert [(o.cash_amount, o.term_months) for o in parsed][:3] == [
        (D("25000.00"), 12), (D("25000.00"), 24), (D("50000.00"), 12)
    ]  # fmt: skip
    assert all(o.expires_at == EXPIRES for o in parsed)


@pytest.mark.parametrize(
    "tamper",
    [
        {"key_cost": "0.00", "financed_principal": "50000.00"},  # llave omitida
        {"financed_principal": "56000.00"},  # llave sumada dos veces
        {"annual_nominal_rate": "0.20"},  # tasa distinta a la del perfil
        {"regular_payment": "1.00"},  # cuota que no cuadra con el calendario
        {"schedule": []},  # sin calendario
    ],
)
def test_any_incoherent_option_rejects_the_whole_response(tamper):
    offers = _mock_offers()
    offers[3] = {**offers[3], **tamper}
    with pytest.raises(InvalidOffers):
        _parse(offers)


def test_options_above_cap_or_capacity_are_rejected():
    with pytest.raises(InvalidOffers):
        _parse(_mock_offers(), max_financed_principal=D("40000.00"))
    with pytest.raises(InvalidOffers):
        _parse(_mock_offers(), payment_capacity=D("2000.00"))


def test_offer_hash_changes_with_any_displayed_value():
    display = _parse(_mock_offers())[0].display()
    assert offer_hash(display) == offer_hash(dict(display))
    assert offer_hash({**display, "regular_payment": "1.00"}) != offer_hash(display)


def test_middle_payments_cannot_hide_an_over_capacity_installment_in_the_total():
    offers = _mock_offers()
    offer = next(o for o in offers if o["cash_amount"] == "50000.00" and o["term_months"] == 24)
    for index, delta in ((1, "5000"), (2, "-2500"), (3, "-2500")):
        row = offer["schedule"][index]
        row["payment"] = str(D(row["payment"]) + D(delta))
    with pytest.raises(InvalidOffers):
        _parse(offers)


@pytest.mark.parametrize("tamper", [
    {"interest": "0.00"}, {"balance": "0.00"}, {"month": 99}, {"month": 2.5},
    {"principal": "NaN"}, {"payment": "Infinity"}, {"balance": "-1.00"},
])
def test_each_schedule_row_must_be_consistent(tamper):
    offers = _mock_offers()
    offers[0]["schedule"][1].update(tamper)
    with pytest.raises(InvalidOffers):
        _parse(offers)


@pytest.mark.parametrize("schedule", [None, [None], ["not a row"]])
def test_malformed_schedule_is_a_provider_error(schedule):
    offers = _mock_offers()
    offers[0]["schedule"] = schedule
    with pytest.raises(InvalidOffers):
        _parse(offers)


@pytest.mark.parametrize("term", [12.5, True, "12.5"])
def test_term_is_an_integer_not_a_truncated_value(term):
    offers = _mock_offers()
    offers[0]["term_months"] = term
    with pytest.raises(InvalidOffers):
        _parse(offers)


@pytest.mark.parametrize("regular, expected", [("102.00", True), ("100.00", False)])
def test_single_installment_must_match_both_declared_payments(regular, expected):
    schedule = [{"month": 1, "payment": "102.00", "principal": "100.00",
                 "interest": "2.00", "balance": "0.00"}]
    assert schedule_consistent(
        schedule, principal=D("100.00"), term_months=1, regular_payment=D(regular),
        last_payment=D("102.00"), total_payment=D("102.00"),
    ) is expected
