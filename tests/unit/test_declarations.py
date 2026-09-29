import pytest
from pydantic import ValidationError

from app.domain.declarations import (
    bureau_fingerprint,
    diff_fields,
    invalidations_for,
    parse_partial,
    payload_hash,
)
from app.domain.states import WorkflowState as S

PROFILE = {
    "full_name": "Ana Prueba López",
    "address": {
        "street": "Calle Demo",
        "external_number": "123",
        "internal_number": None,
        "neighborhood": "Colonia Ejemplo",
        "municipality": "Ciudad de México",
        "state": "CDMX",
        "postal_code": "00000",
        "country": "MX",
    },
    "employment": "SALARIED",
    "employer_or_activity": "Empresa Sintética",
    "income": {"amount": "20000.00", "currency": "MXN", "period": "MONTHLY", "basis": "NET"},
}


def test_partial_payload_keeps_only_given_fields_and_explicit_null():
    assert parse_partial("vehicle", {"has_second_key": None}) == {"has_second_key": None}
    assert parse_partial("vehicle", {"owned_by_customer": True}) == {"owned_by_customer": True}


def test_vehicle_ref_is_normalized():
    assert parse_partial("vehicle", {"vehicle_ref": " abc-123 x "}) == {"vehicle_ref": "ABC123X"}


@pytest.mark.parametrize(
    ("group", "payload"),
    [
        ("vehicle", {"unknown_field": 1}),
        ("profile", {"income": {**PROFILE["income"], "currency": "USD"}}),
        ("profile", {"income": {**PROFILE["income"], "basis": "GROSS"}}),
        ("profile", {"income": {**PROFILE["income"], "amount": "0"}}),
        ("profile", {"income": {**PROFILE["income"], "amount": "10.001"}}),
        # El cliente no declara monto: las opciones las da el cotizador (decisión 0038).
        ("profile", {"requested_cash_amount": "50000.00"}),
        ("profile", {"address": {**PROFILE["address"], "postal_code": "123"}}),
        ("vehicle", {"year": 1970}),
        ("vehicle", {"vehicle_ref": "AB"}),
        ("profile", {"full_name": "Ana"}),
        ("profile", {"address": {**PROFILE["address"], "street": "Calle Demo 123"}}),
    ],
)
def test_invalid_payloads_are_rejected(group, payload):
    with pytest.raises(ValidationError):
        parse_partial(group, payload)


def test_api_is_the_single_source_of_capture_limits():
    from datetime import date

    for ref in ("ABC", "A" * 40):
        assert parse_partial("vehicle", {"vehicle_ref": ref})["vehicle_ref"] == ref
    assert parse_partial("vehicle", {"year": date.today().year + 1})
    with pytest.raises(ValidationError):
        parse_partial("vehicle", {"year": date.today().year + 2})
    assert parse_partial("vehicle", {"owned_by_customer": False}) == {"owned_by_customer": False}
    assert parse_partial("profile", PROFILE)["address"]["postal_code"] == "00000"


def test_capture_errors_expose_location_and_reason_not_customer_input():
    from app.tools.declarations import _validated_fields
    from app.tools.errors import ActionError

    with pytest.raises(ActionError) as exc:
        _validated_fields("profile", {"full_name": "PrivateName"})
    assert "full_name" in exc.value.message and "apellido" in exc.value.message
    assert "PrivateName" not in exc.value.message


def test_decimals_are_canonical_so_equal_values_produce_no_diff():
    current = parse_partial("profile", PROFILE)
    again = parse_partial(
        "profile",
        {"income": {**PROFILE["income"], "amount": "20000"}, "full_name": " Ana  Prueba López "},
    )
    assert diff_fields(current, again) == set()


def test_diff_detects_changed_fields_only():
    current = parse_partial("profile", PROFILE)
    proposed = parse_partial(
        "profile", {"employer_or_activity": "Otra Empresa", "full_name": "Ana Prueba López"}
    )
    assert diff_fields(current, proposed) == {"employer_or_activity"}


@pytest.mark.parametrize(
    ("diff", "stage", "must_include", "must_exclude"),
    [
        (set(), None, set(), {"offers"}),
        ({"income"}, S.PROFILING, {"bureau_if_fingerprint_changed", "profile"}, {"eligibility"}),
        ({"income", "employment"}, S.PROFILING, {"profile", "offers"}, {"eligibility"}),
        (
            {"has_second_key", "income"},
            S.VEHICLE_ELIGIBILITY,
            {"eligibility", "key_quote", "profile"},
            set(),
        ),
        ({"model"}, S.VEHICLE_ELIGIBILITY, {"key_quote"}, set()),
    ],
)
def test_invalidations_follow_tdd_table(diff, stage, must_include, must_exclude):
    deps, resume = invalidations_for(diff)
    assert resume == stage
    assert must_include <= deps
    assert not (must_exclude & deps)


def test_bureau_fingerprint_ignores_employer_but_not_income():
    base = parse_partial("profile", PROFILE)
    fp = bureau_fingerprint(base)
    assert bureau_fingerprint({**base, "employer_or_activity": "Otra"}) == fp
    changed = {**base, "income": {**base["income"], "amount": "25000.00"}}
    assert bureau_fingerprint(changed) != fp


def test_equivalent_income_periods_share_fingerprint():
    base = parse_partial("profile", PROFILE)
    semimonthly = {
        **base,
        "income": {**base["income"], "amount": "10000.00", "period": "SEMIMONTHLY"},
    }
    assert bureau_fingerprint(semimonthly) == bureau_fingerprint(base)


def test_payload_hash_is_stable_and_group_scoped():
    fields = {"owned_by_customer": True}
    assert payload_hash("vehicle", fields) == payload_hash("vehicle", dict(fields))
    assert payload_hash("vehicle", fields) != payload_hash("profile", fields)
