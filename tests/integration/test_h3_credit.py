from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api.main import create_app
from app.domain.clock import FakeClock
from tests.integration.test_h2_cases import FIXED, PROFILE, Api, h, pick


@pytest.fixture(scope="module")
def clock():
    return FakeClock(FIXED)


@pytest.fixture(scope="module")
def client(settings, demo_credentials, clock):
    fast = settings.model_copy(
        update={
            "rate_limit_per_minute": 10_000,
            "read_rate_limit_per_minute": 10_000,
            "provider_retry_delays": [0.0, 0.0],
        }
    )
    with TestClient(create_app(fast, clock=clock)) as c:
        yield c


@pytest.fixture
def ana(client, demo_credentials):
    return Api(client, demo_credentials["cliente-ana"])


@pytest.fixture
def advisor(client, demo_credentials):
    return Api(client, demo_credentials["asesor-1"])


@pytest.fixture
def db(test_database_url):
    engine = create_engine(test_database_url)
    yield engine
    engine.dispose()


VEHICLE_WITH_KEY = {
    "owned_by_customer": True,
    "blocking_debt": False,
    "has_second_key": True,
    "vehicle_ref": "ABC123",
    "make": "Nissan",
    "model": "Versa",
    "year": 2020,
}


def _profile(**overrides):
    return {**PROFILE, **overrides}


def start(api: Api, *, vehicle=None, profile=None) -> dict:
    snap = api.create()
    snap = api.declare(snap, "vehicle", vehicle or VEHICLE_WITH_KEY)
    return api.declare(snap, "profile", profile or PROFILE)


def unique_ref(prefix="VIN") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10].upper()}"


def events(api: Api, advisor: Api, case_id: str) -> list[dict]:
    r = advisor.c.get(f"/cases/{case_id}/events", headers=h(advisor.t, key=False))
    return r.json()["items"]


def select(api: Api, snap: dict, offer: dict, *, key=None, hash_=None):
    headers = h(api.t, version=snap["case_version"])
    if key:
        headers["Idempotency-Key"] = key
    return api.c.post(
        f"/cases/{snap['case_id']}/selections",
        headers=headers,
        json={
            "offer_id": offer["offer_id"],
            "displayed_offer_hash": hash_ or offer["display_hash"],
        },
    )


def run(api: Api, snap: dict) -> dict:
    r = api.c.post(f"/cases/{snap['case_id']}/runs", headers=h(api.t))
    assert r.status_code == 200, r.text
    return r.json()


def ledger(db, provider: str, case_marker: str | None = None):
    with db.connect() as conn:
        return conn.execute(
            text(
                "SELECT attempts, effects FROM mock_provider_ledger "
                "WHERE provider = :p ORDER BY created_at DESC LIMIT 1"
            ),
            {"p": provider},
        ).one()


def test_d01_with_second_key_generates_offers_without_key_quote(ana, advisor):
    snap = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    assert snap["workflow_state"] == "SIMULATION"
    assert snap["credit_profile"]["band"] == "A"
    assert "score" not in snap["credit_profile"]  # el cliente no ve el score
    assert snap["key_quote"] is None
    # Opciones del cotizador (el cliente no declara monto): dentro del tope de 100k del perfil
    # A y de la capacidad de pago de 6,000 (30 % de 20,000).
    assert [(o["cash_amount"], o["term_months"]) for o in snap["offers"]] == [
        ("25000.00", 12), ("25000.00", 24), ("50000.00", 12), ("50000.00", 24),
        ("75000.00", 24), ("100000.00", 24),
    ]  # fmt: skip
    assert {o["key_cost"] for o in snap["offers"]} == {"0.00"}
    assert snap["next_action"] == {"type": "SELECT_OFFER"}
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert "BUREAU_QUERIED" in types and "KEY_QUOTED" not in types
    assert "OFFERS_GENERATED" in types

    offer24 = pick(snap)
    assert (offer24["regular_payment"], offer24["last_payment"], offer24["total_payment"]) == (
        "2643.55",
        "2643.72",
        "63445.37",
    )
    r = select(ana, snap, offer24)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["workflow_state"] == "DOCUMENT_COLLECTION"
    assert done["selection"]["offer_id"] == offer24["offer_id"]
    assert done["next_action"] == {
        "type": "UPLOAD_DOCUMENTS",
        "missing": ["IDENTITY", "INCOME", "VEHICLE_OWNERSHIP"],
    }

    advisor_view = advisor.get(snap["case_id"]).json()
    assert advisor_view["credit_profile"]["score"] == 720


def test_d04_missing_key_is_quoted_once_and_financed_in_principal(ana, advisor, db):
    vehicle = {**VEHICLE_WITH_KEY, "has_second_key": False, "vehicle_ref": unique_ref()}
    snap = start(ana, vehicle=vehicle)
    assert snap["workflow_state"] == "SIMULATION"
    assert snap["key_quote"]["amount"] == "3000.00"
    assert {o["key_cost"] for o in snap["offers"]} == {"3000.00"}
    assert ("100000.00", 24) not in {(o["cash_amount"], o["term_months"]) for o in snap["offers"]}
    offer24 = pick(snap)
    assert offer24["cash_amount"] == "50000.00"
    assert offer24["key_cost"] == "3000.00"
    assert offer24["financed_principal"] == "53000.00"
    assert (offer24["regular_payment"], offer24["last_payment"], offer24["total_payment"]) == (
        "2802.17",
        "2802.11",
        "67252.02",
    )
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert types.count("KEY_QUOTED") == 1
    assert ledger(db, "mock_key_quote").effects == 1
    assert ledger(db, "mock_offers").effects == 1  # las opciones las dio el cotizador


def test_selection_guards_and_idempotency(ana, client, demo_credentials):
    snap = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    offer = snap["offers"][0]

    r = select(ana, snap, offer, hash_="0" * 64)
    assert r.status_code == 409 and r.json()["code"] == "OFFER_HASH_MISMATCH"

    other = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    r = select(ana, snap, other["offers"][0])
    assert r.status_code == 404  # oferta de otro caso: no se revela

    beto = Api(client, demo_credentials["cliente-beto"])
    assert select(beto, snap, offer).status_code == 404

    key = f"sel-{uuid.uuid4()}"
    first = select(ana, snap, offer, key=key)
    assert first.status_code == 200
    replay = select(ana, snap, offer, key=key)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true"

    # D19: una oferta propia no se puede elegir fuera de la etapa de simulación.
    after = first.json()
    r = select(ana, after, snap["offers"][1])
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATE"


def test_d19_selection_outside_simulation_is_rejected(ana):
    snap = ana.create()
    snap = ana.declare(snap, "vehicle", {**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    other = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    r = select(ana, snap, other["offers"][0])
    assert r.status_code == 404  # la oferta no pertenece al caso
    assert ana.get(snap["case_id"]).json()["workflow_state"] == "PROFILING"


def test_d24_expired_offer_cannot_be_selected_and_is_regenerated(ana, clock):
    snap = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    old = snap["offers"][0]
    clock.advance(days=8)
    try:
        r = select(ana, snap, old)
        assert r.status_code == 409 and r.json()["code"] == "OFFER_EXPIRED"
        fresh = run(ana, snap)
        assert fresh["workflow_state"] == "SIMULATION"
        assert fresh["offers"] and fresh["offers"][0]["offer_id"] != old["offer_id"]
        assert select(ana, fresh, fresh["offers"][0]).status_code == 200
    finally:
        clock.advance(days=-8)


def test_d16_transient_timeout_then_success_single_effect(ana, db):
    snap = start(ana, profile=_profile(full_name="Carla Timeout Prueba"))
    assert snap["workflow_state"] == "SIMULATION"
    row = ledger(db, "mock_bureau")
    assert (row.attempts, row.effects) == (2, 1)


def test_d18_lost_response_is_reconciled_without_second_effect(ana, advisor, db):
    snap = start(ana, profile=_profile(full_name="Diego Perdido Prueba"))
    assert snap["workflow_state"] == "SIMULATION"
    row = ledger(db, "mock_bureau")
    assert (row.attempts, row.effects) == (2, 1)
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert types.count("BUREAU_QUERIED") == 1


def test_provider_down_pauses_without_rejection_and_retry_is_explicit(ana, advisor):
    snap = start(ana, profile=_profile(full_name="Hugo Siempre Caido"))
    assert snap["workflow_state"] == "PROFILING"
    assert snap["wait_reason"] == "PROVIDER_RETRY"
    assert snap["next_action"]["type"] == "RETRY"
    again = run(ana, snap)
    assert again["workflow_state"] == "PROFILING"
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert types.count("PROVIDER_CALL_FAILED") == 2
    assert "CASE_REJECTED" not in types


def test_d17_malformed_bureau_response_is_safe_error(ana, advisor):
    snap = start(ana, profile=_profile(full_name="Elena Malformada Prueba"))
    assert snap["workflow_state"] == "PROFILING"
    assert snap["credit_profile"] is None and snap["offers"] == []
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert "PROVIDER_RESPONSE_INVALID" in types
    assert "CASE_REJECTED" not in types and "OFFERS_GENERATED" not in types


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Fer Revision Prueba", "BUREAU_STATUS_REVIEW"),
        ("Gabo Moroso Prueba", "BUREAU_DELINQUENCIES"),
    ],
)
def test_d17_review_profile_goes_to_human_review_not_rejection(ana, advisor, name, reason):
    snap = start(ana, profile=_profile(full_name=name))
    assert snap["workflow_state"] == "HUMAN_REVIEW"
    assert snap["next_action"] == {"type": "WAIT_REVIEW"}
    requested = [
        e
        for e in events(ana, advisor, snap["case_id"])
        if e["event_type"] == "HUMAN_REVIEW_REQUESTED"
    ]
    assert requested[-1]["payload"]["reason_code"] == reason


def test_d23_income_change_after_selection_revokes_and_requeries(ana, advisor):
    snap = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    chosen = select(ana, snap, pick(snap)).json()
    assert chosen["workflow_state"] == "DOCUMENT_COLLECTION"

    income = {**PROFILE["income"], "amount": "30000.00"}
    requeried = ana.declare(chosen, "profile", {"income": income})
    assert requeried["workflow_state"] == "SIMULATION"
    assert requeried["selection"] is None
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert types.count("BUREAU_QUERIED") == 2


def test_no_offers_from_the_quoter_goes_to_an_advisor_not_a_rejection(ana, advisor):
    low = {**PROFILE["income"], "amount": "3000.00"}  # capacidad 900: ninguna opción cabe
    snap = start(ana, profile=_profile(income=low))
    assert snap["workflow_state"] == "HUMAN_REVIEW" and snap["offers"] == []
    requested = [
        e for e in events(ana, advisor, snap["case_id"])
        if e["event_type"] == "HUMAN_REVIEW_REQUESTED"
    ]  # fmt: skip
    assert requested[-1]["payload"]["reason_code"] == "NO_OFFERS_AVAILABLE"
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert "CASE_REJECTED" not in types


def test_the_customer_cannot_declare_an_amount(ana):
    snap = start(ana, vehicle={**VEHICLE_WITH_KEY, "vehicle_ref": unique_ref()})
    r = ana.propose(snap, "profile", {"requested_cash_amount": "40000.00"})
    assert r.status_code == 422


def test_malformed_key_quote_never_simulates_with_zero_cost(ana, advisor):
    vehicle = {**VEHICLE_WITH_KEY, "has_second_key": False, "vehicle_ref": unique_ref("MALQ")}
    snap = start(ana, vehicle=vehicle)
    assert snap["workflow_state"] == "PROFILING"
    assert snap["offers"] == [] and snap["key_quote"] is None
    types = [e["event_type"] for e in events(ana, advisor, snap["case_id"])]
    assert "PROVIDER_RESPONSE_INVALID" in types


def test_missing_vehicle_identity_blocks_key_quote_until_provided(ana):
    snap = ana.create()
    snap = ana.declare(
        snap,
        "vehicle",
        {"owned_by_customer": True, "blocking_debt": False, "has_second_key": False,
         "vehicle_ref": unique_ref()},
    )  # fmt: skip
    snap = ana.declare(snap, "profile", PROFILE)
    assert snap["workflow_state"] == "PROFILING"
    # La placa ya se exigió en la etapa del auto; para cotizar la llave faltan marca, modelo y año.
    assert snap["next_action"] == {
        "type": "PROVIDE_VEHICLE_IDENTITY",
        "fields": ["make", "model", "year"],
    }
    identity = {k: VEHICLE_WITH_KEY[k] for k in ("make", "model", "year")}
    snap = ana.declare(snap, "vehicle", identity)
    assert snap["workflow_state"] == "SIMULATION"
    assert snap["offers"][0]["key_cost"] == "3000.00"
