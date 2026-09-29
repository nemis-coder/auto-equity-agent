"""Presupuesto del agente por caso (TDD §5.4, S10): turnos y tokens persistentes en la API.

El agente de la conversación pide un turno antes de cada llamada al modelo y liquida el uso
real al terminar. Sin presupuesto se pausa y se escala a un asesor; nunca se rechaza.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api.main import create_app
from app.domain.clock import FakeClock
from tests.integration.test_h2_cases import FIXED, Api, h
from tests.integration.test_h4_documents import VEHICLE, _settings
from tests.integration.test_h6_review_gate import as_advisor, resolve


def start(api: Api, snap: dict, key: str | None = None):
    headers = h(api.t)
    if key:
        headers["Idempotency-Key"] = key
    return api.c.post(f"/cases/{snap['case_id']}/agent-turns", headers=headers)


def usage(api: Api, snap: dict, turn_id: str, used_in: int, used_out: int, outcome="OK"):
    body = {"input_tokens": used_in, "output_tokens": used_out, "provider": "anthropic",
            "model": "claude-sonnet-5", "outcome": outcome}  # fmt: skip
    return api.c.post(
        f"/cases/{snap['case_id']}/agent-turns/{turn_id}/usage", headers=h(api.t), json=body
    )


def fresh(api: Api) -> dict:
    """Un caso propio: con el auto declarado, `create()` no reutiliza el de otra prueba."""
    return api.declare(api.create(), "vehicle", VEHICLE)


def counters(engine, case_id: str) -> tuple:
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT tokens_in, tokens_out, tokens_reserved_in, tokens_reserved_out, "
                    "token_allotments FROM cases WHERE id = :c"
                ),
                {"c": case_id},
            ).one()
        )


def test_token_budget_pauses_before_calling_and_resume_grants_allotment(
    settings, demo_credentials, test_database_url
):
    budget = {"case_token_budget_input": 60_000, "case_token_budget_output": 12_000}
    app = create_app(_settings(settings, **budget), clock=FakeClock(FIXED))
    engine = create_engine(test_database_url)
    with TestClient(app) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        adv = Api(c, demo_credentials["asesor-1"])
        snap = fresh(api)
        for _ in range(2):
            r = start(api, snap)
            assert r.status_code == 201, r.text
            assert usage(api, snap, r.json()["turn_id"], 25_000, 300).status_code == 200
        # 50 000 usados + 16 000 reservados superan 60 000: no hay turno ni llamada al modelo.
        r = start(api, snap)
        assert r.status_code == 409 and r.json()["code"] == "TOKEN_BUDGET_EXCEEDED"
        paused = api.get(snap["case_id"]).json()
        assert paused["workflow_state"] == "HUMAN_REVIEW"
        assert paused["wait_reason"] == "BUDGET_EXCEEDED"
        assert paused["open_review"]["reason_code"] == "TOKEN_BUDGET_EXCEEDED"
        assert counters(engine, snap["case_id"]) == (50_000, 600, 0, 0, 1)  # reservas liberadas

        r = resolve(adv, as_advisor(adv, paused), {"resolution": "RESUME"})
        assert r.status_code == 200 and r.json()["workflow_state"] == "PROFILING"
        assert start(api, snap).status_code == 201  # la nueva asignación permite continuar
    engine.dispose()


def test_turn_limit_pauses_and_escalates_without_rejecting(settings, demo_credentials):
    app = create_app(_settings(settings, max_turns_per_case=2), clock=FakeClock(FIXED))
    with TestClient(app) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        adv = Api(c, demo_credentials["asesor-1"])
        snap = fresh(api)
        for _ in range(2):
            turn = start(api, snap).json()["turn_id"]
            usage(api, snap, turn, 1_000, 100)
        r = start(api, snap)
        assert r.status_code == 409 and r.json()["code"] == "TURN_BUDGET_EXCEEDED"
        paused = api.get(snap["case_id"]).json()
        assert paused["workflow_state"] == "HUMAN_REVIEW"
        assert paused["open_review"]["reason_code"] == "TURN_BUDGET_EXCEEDED"
        r = resolve(adv, as_advisor(adv, paused), {"resolution": "RESUME"})
        assert r.status_code == 200 and r.json()["workflow_state"] == "PROFILING"
        assert start(api, snap).status_code == 201  # el conteo se reinicia tras la revisión


def test_turns_are_idempotent_private_and_settled_once(
    settings, demo_credentials, test_database_url
):
    engine = create_engine(test_database_url)
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        ana = Api(c, demo_credentials["cliente-ana"])
        beto = Api(c, demo_credentials["cliente-beto"])
        adv = Api(c, demo_credentials["asesor-1"])
        snap = fresh(ana)
        key = f"test-{uuid.uuid4()}"
        first = start(ana, snap, key=key)
        again = start(ana, snap, key=key)
        assert again.json()["turn_id"] == first.json()["turn_id"]
        assert again.headers["Idempotent-Replay"] == "true"
        turn = first.json()["turn_id"]
        # Ni otro cliente ni el asesor pueden liquidar o pedir turnos de este caso.
        assert usage(beto, snap, turn, 1, 1).status_code == 404
        assert start(adv, snap).status_code in (403, 404)
        assert usage(ana, snap, turn, 4_000, 200).status_code == 200
        assert usage(ana, snap, turn, 9_999, 999).json()["status"] == "SUCCEEDED"  # sin doble cobro
        assert counters(engine, snap["case_id"])[:4] == (4_000, 200, 0, 0)
    engine.dispose()


def test_stale_reservation_is_released(settings, demo_credentials, test_database_url):
    engine = create_engine(test_database_url)
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        turn = start(api, snap).json()["turn_id"]  # el agente cae sin informar el uso
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE operations SET created_at = now() - interval '11 minutes' WHERE id = :t"
                ),
                {"t": turn},
            )
        assert start(api, snap).status_code == 201
        reserved = counters(engine, snap["case_id"])[2]
        assert reserved == 16_000  # solo la reserva del turno nuevo
    engine.dispose()


def test_each_turn_is_traced_without_conversation_text(settings, demo_credentials, tmp_path):
    traced = _settings(settings, trace_dir=str(tmp_path))
    with TestClient(create_app(traced, clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        turn = start(api, snap).json()["turn_id"]
        usage(api, snap, turn, 5_000, 300)
    records = [
        json.loads(line) for p in tmp_path.glob("*.jsonl") for line in p.read_text().splitlines()
    ]
    spans = [r for r in records if r["name"] == "agent.usage"]
    assert len(spans) == 1 and spans[0]["kind"] == "span"
    # El consumo/costo generativo se registra una sola vez, en el agente.
    assert spans[0]["usage"] is None and spans[0]["cost_usd"] is None
    assert spans[0]["metadata"]["input_tokens"] == 5_000
    assert spans[0]["metadata"]["output_tokens"] == 300
    assert spans[0]["metadata"]["model"] == "claude-sonnet-5"
    assert spans[0]["metadata"]["outcome"] == "OK"


def test_interrupted_settlement_rolls_back_and_can_be_retried(
    settings, demo_credentials, test_database_url, monkeypatch
):
    from app.api import agent_turns_routes as routes

    original = routes.settle

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic interruption")

    engine = create_engine(test_database_url)
    with TestClient(create_app(_settings(settings)), raise_server_exceptions=False) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        turn = start(api, snap).json()["turn_id"]
        with monkeypatch.context() as patch:
            patch.setattr(routes, "settle", interrupted)
            assert usage(api, snap, turn, 4000, 200).status_code == 500
        assert counters(engine, snap["case_id"])[:4] == (0, 0, 16000, 1500)
        assert usage(api, snap, turn, 4000, 200).status_code == 200
        assert usage(api, snap, turn, 4000, 200).status_code == 200
        assert counters(engine, snap["case_id"])[:4] == (4000, 200, 0, 0)
    engine.dispose()


def test_interrupted_reservation_does_not_leave_orphaned_tokens(
    settings, demo_credentials, test_database_url, monkeypatch
):
    from app.api import agent_turns_routes as routes

    original = routes.reserve

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic interruption")

    engine = create_engine(test_database_url)
    with TestClient(create_app(_settings(settings)), raise_server_exceptions=False) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        key = f"test-{uuid.uuid4()}"
        with monkeypatch.context() as patch:
            patch.setattr(routes, "reserve", interrupted)
            assert start(api, snap, key).status_code == 500
        assert counters(engine, snap["case_id"])[:4] == (0, 0, 0, 0)
        assert start(api, snap, key).status_code == 201
        assert counters(engine, snap["case_id"])[2:4] == (16000, 1500)
    engine.dispose()


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_starts_respect_idempotency_and_turn_limit(
    settings, demo_credentials, test_database_url, monkeypatch, same_key
):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.api import agent_turns_routes as routes

    original = routes.reserve

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.05)  # fuerza solapamiento antes de reservar
        return await original(*args, **kwargs)

    monkeypatch.setattr(routes, "reserve", delayed)
    engine = create_engine(test_database_url)
    app = create_app(_settings(settings, max_turns_per_case=1))
    with TestClient(app, raise_server_exceptions=False) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        key = f"test-{uuid.uuid4()}"
        barrier = Barrier(2)

        def request(i):
            barrier.wait(timeout=5)
            return start(api, snap, key if same_key else f"{key}-{i}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, range(2)))
        assert sorted(r.status_code for r in results) == ([200, 201] if same_key else [201, 409])
        if same_key:
            assert results[0].json()["turn_id"] == results[1].json()["turn_id"]
        assert counters(engine, snap["case_id"])[2:4] == (16000, 1500)
    engine.dispose()


def test_stale_release_rolls_back_together_and_late_usage_cannot_charge_twice(
    settings, demo_credentials, test_database_url, monkeypatch
):
    from app.api import agent_turns_routes as routes

    original = routes.settle

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic interruption")

    engine = create_engine(test_database_url)
    with TestClient(create_app(_settings(settings)), raise_server_exceptions=False) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = fresh(api)
        turn = start(api, snap).json()["turn_id"]
        with engine.begin() as conn:
            conn.execute(text("UPDATE operations SET created_at = now() - interval '11 minutes' "
                              "WHERE id = :id"), {"id": turn})
        with monkeypatch.context() as patch:
            patch.setattr(routes, "settle", interrupted)
            assert start(api, snap).status_code == 500
        assert counters(engine, snap["case_id"])[2:4] == (16000, 1500)
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT status FROM operations WHERE id = :id"),
                               {"id": turn}) == "PENDING"
        assert start(api, snap).status_code == 201
        assert counters(engine, snap["case_id"])[2:4] == (16000, 1500)
        assert usage(api, snap, turn, 1000, 100).json()["status"] == "FAILED_FINAL"
        assert counters(engine, snap["case_id"])[:4] == (0, 0, 16000, 1500)
    engine.dispose()
