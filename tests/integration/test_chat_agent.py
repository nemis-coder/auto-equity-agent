"""El agente de la conversación de punta a punta contra la API (sin red ni modelo real).

Mismo grafo que sirve Aegra, con un modelo guionado (`evals/chat_agent.py`): los datos solo se
guardan si la persona aprueba la tarjeta, y cada llamada al modelo pasa por el presupuesto del
caso.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.clock import FakeClock
from evals.chat_agent import ChatSession, call
from tests.integration.test_h2_cases import FIXED, Api
from tests.integration.test_h4_documents import VEHICLE, _settings


@pytest.mark.parametrize("lost_responses", [1, 2])
def test_lost_confirmation_response_does_not_duplicate_the_effect(
    settings, demo_credentials, monkeypatch, lost_responses
):
    from evals.chat_agent import TestClientTransport

    original = TestClientTransport.handle_async_request
    keys = []

    async def lose_response(self, request):
        response = await original(self, request)
        if request.url.path.endswith("/confirmations"):
            keys.append(request.headers["Idempotency-Key"])
            if len(keys) <= lost_responses:
                raise httpx.ReadTimeout("synthetic lost response")
        return response

    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        state = chat.say("Mi auto…", [call("proponer_datos_del_auto", **AUTO)])
        cid = state["case_id"]
        before = api.get(cid).json()
        with monkeypatch.context() as patch:
            patch.setattr(TestClientTransport, "handle_async_request", lose_response)
            result = chat.decide(True, ["Datos confirmados."])
        after = api.get(cid).json()
        assert len(keys) == 2 and len(set(keys)) == 1
        assert after["input_revision"] == before["input_revision"] + 1
        assert after["vehicle"]["make"] == "Nissan"
        assert not chat.waiting_for_card()
        if lost_responses == 2:
            assert result["turn_failed"]
            assert chat.model.calls == 0  # no inventar el resultado ni seguir con más comandos
            resumed = chat.say(
                "¿Se guardaron?", [call("consultar_solicitud"), "Ya están guardados."]
            )
            assert not resumed["turn_failed"]
            assert chat.results(resumed, "consultar_solicitud")[-1]["auto"]["make"] == "Nissan"
        assert api.get(cid).json()["input_revision"] == after["input_revision"]

AUTO = {k: VEHICLE[k] for k in ("owned_by_customer", "blocking_debt", "has_second_key",
                                "make", "model", "year", "vehicle_ref")}  # fmt: skip


@pytest.mark.parametrize("fields", [{"year": 1850}, {"vehicle_ref": "AB"}])
def test_invalid_capture_is_rejected_by_api_without_card_or_write(
    settings, demo_credentials, fields
):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        snap = api.create()
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        state = chat.say(
            "Estos son mis datos",
            [call("proponer_datos_del_auto", **{**AUTO, **fields}), "Revisa los datos."],
            case_id=snap["case_id"],
        )
        assert not chat.waiting_for_card()
        assert any("datos_invalidos" in str(m.content) for m in state["messages"])
        after = api.get(snap["case_id"]).json()
        assert after["pending_action"] is None
        assert after["vehicle"]["make"] is None
        assert after["case_version"] == snap["case_version"]


def test_short_reference_uses_same_contract_in_chat_and_api(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        state = chat.say(
            "Mi placa es ABC",
            [call("proponer_datos_del_auto", **{**AUTO, "vehicle_ref": "ABC"})],
        )
        assert chat.waiting_for_card()
        chat.decide(True, ["Listo."])
        assert api.get(state["case_id"]).json()["vehicle"]["vehicle_ref"] == "ABC"


def test_data_is_saved_only_after_the_person_approves_the_card(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        state = chat.say("Mi auto es un Versa 2020…", [call("proponer_datos_del_auto", **AUTO)])
        case_id = state["case_id"]
        assert chat.waiting_for_card()
        assert api.get(case_id).json()["workflow_state"] == "VEHICLE_ELIGIBILITY"
        chat.decide(False, ["¿Qué quieres corregir?"])
        assert api.get(case_id).json()["vehicle"]["make"] is None  # rechazada: nada guardado
        chat.say("Está bien así", [call("proponer_datos_del_auto", **AUTO)])
        chat.decide(True, ["Listo."])
        snap = api.get(case_id).json()
        assert snap["workflow_state"] == "PROFILING" and snap["vehicle"]["make"] == "Nissan"


def test_every_model_call_uses_the_case_budget_and_exhaustion_escalates(settings, demo_credentials):
    app = create_app(_settings(settings, max_turns_per_case=2), clock=FakeClock(FIXED))
    with TestClient(app) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        snap = api.declare(api.create(), "vehicle", VEHICLE)
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        chat.say("hola", ["Hola."], case_id=snap["case_id"])
        chat.say("hola otra vez", ["Hola de nuevo."])
        assert chat.model.calls == 1
        state = chat.say("¿sigues?", ["no debería llamarse"])
        assert chat.model.calls == 0  # sin turno concedido no se llama al modelo
        assert "asesor" in state["messages"][-1].content
        after = api.get(snap["case_id"]).json()
        assert after["workflow_state"] == "HUMAN_REVIEW"
        assert after["open_review"]["reason_code"] == "TURN_BUDGET_EXCEEDED"


def test_progressive_vehicle_does_not_interrupt_until_complete(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        result = chat.say("Sí, está a mi nombre y no tiene adeudos", [
            call("proponer_datos_del_auto", owned_by_customer=True, blocking_debt=False),
            "¿Tienes la segunda llave?",
        ])
        assert not chat.waiting_for_card()
        snap = api.get(result["case_id"]).json()
        assert snap["pending_action"] is None and snap["vehicle"]["owned_by_customer"] is None
        chat.say("El resto de mis datos…", [call("proponer_datos_del_auto", **AUTO)])
        assert chat.waiting_for_card()
        assert api.get(result["case_id"]).json()["vehicle"]["owned_by_customer"] is None
        chat.decide(True, ["Datos confirmados."])
        assert api.get(result["case_id"]).json()["vehicle"]["blocking_debt"] is False


def test_empty_proposal_repairs_from_same_turn_and_keeps_false(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-beto"])
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        result = chat.say("No, el auto no está a mi nombre", [
            call("proponer_datos_del_auto"),
            call("proponer_datos_del_auto", owned_by_customer=False),
        ])
        assert chat.waiting_for_card() and chat.model.calls == 2
        snap = api.get(result["case_id"]).json()
        assert snap["workflow_state"] == "VEHICLE_ELIGIBILITY"
        assert snap["pending_action"]["fields"] == {"owned_by_customer": False}
        result = chat.decide(True, ["REJECTED. Te llamarán hoy."])
        assert "no está a tu nombre" in result["messages"][-1].content
        assert "REJECTED" not in result["messages"][-1].content


def test_empty_proposal_repair_is_bounded(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        chat = ChatSession(c, demo_credentials["cliente-beto"])
        result = chat.say("No está a mi nombre", [call("proponer_datos_del_auto")] * 3)
        assert chat.model.calls == 2
        assert result["turn_failed"] and not chat.waiting_for_card()
        assert "No se guardó" in result["messages"][-1].content
