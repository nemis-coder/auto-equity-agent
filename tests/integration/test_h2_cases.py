from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.api.main import create_app
from app.domain.clock import FakeClock

FIXED = datetime(2026, 9, 24, 12, tzinfo=UTC)
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


@pytest.fixture(scope="module")
def clock():
    return FakeClock(FIXED)


@pytest.fixture(scope="module")
def client(settings, demo_credentials, clock):
    with TestClient(
        create_app(settings.model_copy(update={"rate_limit_per_minute": 10_000}), clock=clock)
    ) as c:
        yield c


def h(token: str, *, key: bool = True, version: int | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = f"test-{uuid.uuid4()}"
    if version is not None:
        headers["If-Match"] = str(version)
    return headers


def pick(snap: dict, term: int = 24, cash: str = "50000.00") -> dict:
    """La opción del cotizador con ese plazo y ese efectivo (el cliente no declara monto)."""
    return next(
        o for o in snap["offers"]
        if o["term_months"] == term and o["cash_amount"] == cash and not o["expired"]
    )  # fmt: skip


class Api:
    def __init__(self, client: TestClient, token: str) -> None:
        self.c = client
        self.t = token

    def create(self) -> dict:
        r = self.c.post("/cases", headers=h(self.t))
        assert r.status_code in (200, 201), r.text  # 200: reutiliza una solicitud sin empezar
        return r.json()

    def get(self, case_id: str) -> httpx.Response:
        return self.c.get(f"/cases/{case_id}", headers=h(self.t, key=False))

    def propose(self, snap: dict, group: str, fields: dict) -> httpx.Response:
        return self.c.post(
            f"/cases/{snap['case_id']}/declaration-proposals",
            headers=h(self.t, version=snap["case_version"]),
            json={"group": group, "fields": fields},
        )

    def confirm(self, snap: dict) -> httpx.Response:
        pa = snap["pending_action"]
        return self.c.post(
            f"/cases/{snap['case_id']}/confirmations",
            headers=h(self.t, version=snap["case_version"]),
            json={"pending_action_id": pa["pending_action_id"], "payload_hash": pa["payload_hash"]},
        )

    def declare(self, snap: dict, group: str, fields: dict) -> dict:
        r = self.propose(snap, group, fields)
        assert r.status_code == 200, r.text
        r = self.confirm(r.json())
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def ana(client, demo_credentials):
    return Api(client, demo_credentials["cliente-ana"])


@pytest.fixture
def beto(client, demo_credentials):
    return Api(client, demo_credentials["cliente-beto"])


def _events(client, token, case_id):
    r = client.get(f"/cases/{case_id}/events", headers=h(token, key=False))
    assert r.status_code == 200
    return r.json()["items"]


def test_new_case_starts_in_vehicle_eligibility(ana):
    snap = ana.create()
    assert snap["workflow_state"] == "VEHICLE_ELIGIBILITY"
    assert snap["case_version"] == 1
    assert snap["next_action"] == {
        "type": "PROVIDE_VEHICLE_ANSWERS",
        "fields": ["owned_by_customer", "blocking_debt", "has_second_key"],
    }
    assert snap["progress"][0] == {"stage": "VEHICLE_ELIGIBILITY", "status": "current"}


def test_d02_vehicle_not_owned_is_rejected_and_case_closes(ana, client, demo_credentials):
    snap = ana.create()
    snap = ana.declare(
        snap,
        "vehicle",
        {"owned_by_customer": False, "blocking_debt": False, "has_second_key": True},
    )
    assert snap["workflow_state"] == "REJECTED"
    assert snap["eligibility"]["rejection_reasons"] == ["VEHICLE_NOT_OWNED"]
    assert snap["next_action"] == {"type": "NONE"}
    r = ana.propose(snap, "vehicle", {"owned_by_customer": True})
    assert r.status_code == 409 and r.json()["code"] == "TERMINAL_CASE"
    types = [
        e["event_type"] for e in _events(client, demo_credentials["cliente-ana"], snap["case_id"])
    ]
    assert "CASE_REJECTED" in types and "STAGE_ENTERED" not in types


def test_d03_blocking_debt_is_rejected_with_distinct_reason(ana):
    snap = ana.create()
    snap = ana.declare(
        snap, "vehicle", {"owned_by_customer": True, "blocking_debt": True, "has_second_key": True}
    )
    assert snap["workflow_state"] == "REJECTED"
    assert snap["eligibility"]["rejection_reasons"] == ["BLOCKING_DEBT"]


def test_unknown_answer_asks_again_and_missing_key_is_not_rejection(ana):
    snap = ana.create()
    snap = ana.declare(snap, "vehicle", {"owned_by_customer": True, "blocking_debt": None})
    assert snap["workflow_state"] == "VEHICLE_ELIGIBILITY"
    assert snap["eligibility"]["status"] == "PENDING"
    assert snap["next_action"]["fields"] == ["blocking_debt", "has_second_key"]
    snap = ana.declare(snap, "vehicle", {"blocking_debt": False, "has_second_key": False})
    # El auto califica, pero sin placa no sale de la etapa: el documento no se podría cotejar.
    assert snap["workflow_state"] == "VEHICLE_ELIGIBILITY"
    assert snap["eligibility"]["status"] == "PENDING"
    assert snap["next_action"] == {"type": "PROVIDE_VEHICLE_ANSWERS", "fields": ["vehicle_ref"]}
    snap = ana.declare(snap, "vehicle", {"vehicle_ref": "ABC-123-XYZ"})
    assert snap["workflow_state"] == "PROFILING"
    assert "declared_owner_name" not in snap["missing_fields"]["vehicle"]  # opcional
    assert snap["eligibility"]["status"] == "ELIGIBLE"
    assert snap["eligibility"]["key_quote_required"] is True
    assert snap["vehicle"]["owned_by_customer"] is True  # campo omitido conserva su valor


def _eligible(api: Api) -> dict:
    snap = api.create()
    return api.declare(
        snap,
        "vehicle",
        {"owned_by_customer": True, "blocking_debt": False, "has_second_key": True,
         "vehicle_ref": "ABC-123-XYZ"},
    )  # fmt: skip


def test_profile_partial_diff_and_invalidations(ana, client, demo_credentials):
    snap = _eligible(ana)
    snap = ana.declare(snap, "profile", PROFILE)
    # Con H3, el perfil completo avanza a Buró y simulación en el mismo turno.
    assert snap["workflow_state"] == "SIMULATION"
    assert snap["missing_fields"]["profile"] == []
    revision = snap["input_revision"]

    same = ana.declare(snap, "profile", {"employer_or_activity": "Empresa Sintética"})
    assert same["input_revision"] == revision

    changed = ana.declare(same, "profile", {"employer_or_activity": "Otra Empresa Sintética"})
    assert changed["input_revision"] == revision + 1
    assert changed["profile"]["employer_or_activity"] == "Otra Empresa Sintética"
    assert changed["profile"]["full_name"] == PROFILE["full_name"]

    back = ana.declare(changed, "vehicle", {"has_second_key": False})
    assert back["workflow_state"] == "PROFILING"  # vuelve a elegibilidad y se reevalúa
    assert back["eligibility"]["key_quote_required"] is True
    assert back["offers"] == []
    assert back["next_action"]["type"] == "PROVIDE_VEHICLE_IDENTITY"
    events = _events(client, demo_credentials["asesor-1"], back["case_id"])
    invalidated = [e for e in events if e["event_type"] == "DEPENDENCIES_INVALIDATED"]
    assert invalidated[-1]["payload"] == {
        "dependencies": ["eligibility", "offers"],
        "from_state": "SIMULATION",
        "to_state": "VEHICLE_ELIGIBILITY",
    }


def test_null_after_use_is_rejected(ana):
    snap = _eligible(ana)
    r = ana.propose(snap, "vehicle", {"owned_by_customer": None})
    assert r.status_code == 422 and r.json()["code"] == "NULL_AFTER_USE"


def test_invalid_payload_names_fields_without_echoing_values(ana):
    snap = ana.create()
    r = ana.propose(snap, "profile", {"income": {**PROFILE["income"], "currency": "USD"}})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_DECLARATION"
    assert "income" in r.json()["message"] and "USD" not in r.json()["message"]


def test_isolation_between_customers_and_roles(ana, beto, client, demo_credentials):
    snap = ana.create()
    case_id = snap["case_id"]
    assert beto.get(case_id).status_code == 404
    assert beto.propose(snap, "vehicle", {"owned_by_customer": True}).status_code == 404
    assert (
        client.get(
            f"/cases/{case_id}/events", headers=h(demo_credentials["cliente-beto"], key=False)
        ).status_code
        == 404
    )

    advisor = Api(client, demo_credentials["asesor-1"])
    assert advisor.get(case_id).status_code == 200
    r = advisor.propose(snap, "vehicle", {"owned_by_customer": True})
    assert r.status_code == 403

    service = Api(client, demo_credentials["agente-servicio"])
    assert service.get(case_id).status_code == 404
    assert client.post("/cases", headers=h(demo_credentials["asesor-1"])).status_code == 403

    listed = client.get("/cases", headers=h(demo_credentials["cliente-beto"], key=False)).json()
    assert case_id not in {c["case_id"] for c in listed["items"]}


def test_random_case_id_is_404_not_500(ana):
    assert ana.get(str(uuid.uuid4())).status_code == 404


def test_version_and_idempotency_contract(ana, client, demo_credentials):
    token = demo_credentials["cliente-ana"]
    snap = ana.create()
    url = f"/cases/{snap['case_id']}/declaration-proposals"
    body = {"group": "vehicle", "fields": {"owned_by_customer": True}}

    r = client.post(url, headers={**h(token), "If-Match": "99"}, json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "VERSION_CONFLICT" and r.json()["current_version"] == 1

    assert client.post(url, headers=h(token), json=body).status_code == 428
    assert client.post(url, headers=h(token, key=False, version=1), json=body).status_code == 428

    headers = h(token, version=1)
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 200
    replay = client.post(url, headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == first.json()

    conflict = client.post(
        url, headers=headers, json={**body, "fields": {"owned_by_customer": False}}
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    assert ana.get(snap["case_id"]).json()["case_version"] == 2  # el replay no mutó


def test_create_case_replay_does_not_duplicate(client, demo_credentials):
    headers = h(demo_credentials["cliente-beto"])
    a = client.post("/cases", headers=headers)
    b = client.post("/cases", headers=headers)
    assert a.status_code == b.status_code == 201
    assert a.json()["case_id"] == b.json()["case_id"]


def test_pending_action_guards(ana, clock):
    snap = ana.create()
    proposed = ana.propose(snap, "vehicle", {"owned_by_customer": True}).json()

    bad = dict(proposed, pending_action={**proposed["pending_action"], "payload_hash": "0" * 64})
    r = ana.confirm(bad)
    assert r.status_code == 409 and r.json()["code"] == "PAYLOAD_HASH_MISMATCH"

    newer = ana.propose(proposed, "vehicle", {"owned_by_customer": False}).json()
    stale = dict(newer, pending_action=proposed["pending_action"])
    r = ana.confirm(stale)
    assert r.status_code == 409 and r.json()["code"] == "PENDING_ACTION_NOT_ACTIVE"

    clock.advance(minutes=16)
    try:
        r = ana.confirm(newer)
        assert r.status_code == 409 and r.json()["code"] == "PENDING_ACTION_EXPIRED"
        assert ana.get(snap["case_id"]).json()["pending_action"] is None
    finally:
        clock.advance(minutes=-16)


def test_case_events_are_append_only(test_database_url, ana):
    ana.create()
    engine = create_engine(test_database_url)
    try:
        for stmt in (
            "UPDATE case_events SET event_type = 'X'",
            "DELETE FROM case_events",
            "TRUNCATE case_events",
        ):
            with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


def test_rate_limits_split_reads_and_writes_using_real_time(settings, demo_credentials):
    limited = settings.model_copy(
        update={"rate_limit_per_minute": 2, "read_rate_limit_per_minute": 3}
    )
    token = demo_credentials["cliente-beto"]
    with TestClient(create_app(limited, clock=FakeClock(FIXED))) as c:
        reads = [c.get("/me", headers=h(token, key=False)).status_code for _ in range(4)]
        writes = [c.post("/cases", headers=h(token)).status_code for _ in range(3)]
    assert reads == [200, 200, 200, 429]
    # El cupo de escritura es independiente del de lectura. La segunda creación reutiliza la
    # solicitud sin empezar (200), pero igual consume cupo.
    assert writes[0] in (200, 201) and writes[1:] == [200, 429]


def test_new_request_reuses_an_unstarted_case(settings, demo_credentials):
    with TestClient(create_app(settings.model_copy(update={"rate_limit_per_minute": 10_000}),
                               clock=FakeClock(FIXED))) as c:  # fmt: skip
        api = Api(c, demo_credentials["cliente-beto"])
        first = api.create()
        again = c.post("/cases", headers=h(api.t))
        assert again.status_code == 200 and again.json()["reused_draft"] is True
        assert again.json()["case_id"] == first["case_id"]
        # En cuanto la solicitud tiene datos confirmados, «Nueva solicitud» crea otra.
        api.declare(first, "vehicle", {"owned_by_customer": True, "blocking_debt": False,
                                       "has_second_key": True})  # fmt: skip
        fresh = c.post("/cases", headers=h(api.t))
        assert fresh.status_code == 201 and fresh.json()["case_id"] != first["case_id"]


async def test_concurrent_confirmations_only_one_wins(settings, demo_credentials):
    app = create_app(
        settings.model_copy(update={"rate_limit_per_minute": 10_000}), clock=FakeClock(FIXED)
    )
    token = demo_credentials["cliente-ana"]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            snap = (await c.post("/cases", headers=h(token))).json()
            snap = (
                await c.post(
                    f"/cases/{snap['case_id']}/declaration-proposals",
                    headers=h(token, version=snap["case_version"]),
                    json={"group": "vehicle", "fields": {"owned_by_customer": True}},
                )
            ).json()
            pa = snap["pending_action"]
            body = {
                "pending_action_id": pa["pending_action_id"],
                "payload_hash": pa["payload_hash"],
            }
            url = f"/cases/{snap['case_id']}/confirmations"
            results = await asyncio.gather(
                c.post(url, headers=h(token, version=snap["case_version"]), json=body),
                c.post(url, headers=h(token, version=snap["case_version"]), json=body),
            )
    assert sorted(r.status_code for r in results) == [200, 409]
