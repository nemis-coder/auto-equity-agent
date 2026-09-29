"""Un recorrido del grafo real, con modelo guionado, comparte traza con la API."""

import base64
import json
from dataclasses import replace

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from app.api.main import create_app
from app.domain.clock import FakeClock
from app.observability import agent as telemetry
from app.observability.settings import ModelPrice
from app.observability.traces import JsonlSink, Tracer
from app.providers.extraction import FakeExtractionProvider
from evals.chat_agent import ChatSession, call, load_graph
from tests.integration.test_h2_cases import FIXED, Api
from tests.integration.test_h4_documents import DOCS, _settings, to_documents


def test_chat_model_tools_api_share_trace_and_do_not_double_count(
    settings, demo_credentials, tmp_path, monkeypatch,
):
    graph = load_graph()
    agent_tracer = Tracer(sink=JsonlSink(tmp_path / "agent"))
    api_tracer = Tracer(sink=JsonlSink(tmp_path / "api"))
    monkeypatch.setattr(telemetry, "get_tracer", lambda: agent_tracer)
    monkeypatch.setattr(graph, "get_tracer", lambda: agent_tracer)
    app = create_app(_settings(settings), tracer=api_tracer, clock=FakeClock(FIXED))
    with TestClient(app) as c:
        chat = ChatSession(c, demo_credentials["cliente-ana"])
        chat.say("Texto privado de la persona", [call("consultar_solicitud"), "Hola"])
    records = [json.loads(line) for p in tmp_path.rglob("*.jsonl")
               for line in p.read_text().splitlines()]
    assert len({r["trace_id"] for r in records}) == 1
    spans = {r["span_id"]: r for r in records}
    requests = [r for r in records if r["name"] == "http.request"]
    assert requests and all(spans[r["parent_span_id"]]["name"] == "agent.api" for r in requests)
    assert any(r["name"] == "agent.tools" for r in records)
    assert len([r for r in records if r["kind"] == "generation"]) == 2
    assert all(r["usage"] is None for r in records if r["name"] == "agent.usage")
    assert "Texto privado" not in str(records)
    assert demo_credentials["cliente-ana"] not in str(records)


def test_invalid_trace_header_does_not_break_api_or_authorize_requests(
    settings, demo_credentials,
):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        response = c.get("/me", headers={"traceparent": "invalid"})
        assert response.status_code == 401
        assert len(response.headers["X-Trace-Id"]) == 32
        assert response.headers["X-Correlation-Id"]


def test_document_chain_keeps_parentage_and_computes_cost(
    settings, demo_credentials, tmp_path, monkeypatch,
):
    class PricedFixture(FakeExtractionProvider):
        async def extract(self, *args):
            result = await super().extract(*args)
            return replace(result, provider="anthropic", model="test", input_tokens=1000,
                           output_tokens=100)

    graph = load_graph()
    tracer = Tracer(sink=JsonlSink(tmp_path), model_prices={
        "anthropic:test": ModelPrice(input=3, output=15),
    })
    monkeypatch.setattr(telemetry, "get_tracer", lambda: tracer)
    monkeypatch.setattr(graph, "get_tracer", lambda: tracer)
    app = create_app(_settings(settings), tracer=tracer, clock=FakeClock(FIXED),
                     extractor=PricedFixture())
    with TestClient(app) as c:
        token = demo_credentials["cliente-ana"]
        snap = to_documents(Api(c, token))
        chat = ChatSession(c, token)
        encoded = base64.b64encode((DOCS / "identity_ana.png").read_bytes()).decode()
        chat._run({"case_id": snap["case_id"], "messages": [HumanMessage(content=[{
            "type": "image", "mimeType": "image/png", "data": encoded,
            "metadata": {"name": "identity_ana.png"},
        }])]}, [call("subir_documento", tipo="IDENTITY", archivo="identity_ana.png"), "Recibido"])
    records = [json.loads(line) for p in tmp_path.glob("*.jsonl")
               for line in p.read_text().splitlines()]
    spans = {r["span_id"]: r for r in records}
    extraction = next(r for r in records if r["name"] == "document.extraction")
    assert extraction["cost_usd"] == 0.0045
    assert extraction["metadata"]["cost_status"] == "estimated"
    assert extraction["trace_id"] == telemetry.conversation_context(chat.config)["trace_id"]
    parent = extraction
    for name in ("http.request", "agent.api", "agent.tools"):
        parent = spans[parent["parent_span_id"]]
        assert parent["name"] == name
    assert encoded not in str(records)
