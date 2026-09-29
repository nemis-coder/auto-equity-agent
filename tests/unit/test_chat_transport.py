"""Mismo agente, SDK y HTTP simulado por proveedor: herramientas, historial y streaming."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.observability.costs import token_usage
from evals.chat_agent import load_graph


def build(llm):
    _, fake = llm()
    g = load_graph()
    return g, g.build_llm(fake.route, "modelo-snapshot", "test-provider-key"), fake


async def test_chat_tool_round_trip_keeps_id_and_optional_arguments(llm):
    g, model, fake = build(llm)
    fake.tool("proponer_datos_del_auto", {"owned_by_customer": True})
    messages = [SystemMessage(g.PROMPT), HumanMessage("El auto está a mi nombre")]
    answer = await model.ainvoke(messages)
    assert answer.tool_calls == [{"name": "proponer_datos_del_auto",
                                 "args": {"owned_by_customer": True},
                                 "id": "call_1", "type": "tool_call"}]
    assert g.after_agent({"messages": [answer]}) == "prepare"  # requiere tarjeta, no guarda
    assert token_usage(answer.usage_metadata) == {"input": 1200, "output": 300}
    if fake.route == "openai":
        assert fake.last["parallel_tool_calls"] is False
        assert all(tool["strict"] is False for tool in fake.last["tools"])
        assert fake.last["store"] is False
        assert fake.last["include"] == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in fake.last
    else:
        assert fake.last["tool_choice"]["disable_parallel_tool_use"] is True
    fake.reply(text="Revisa los datos en la tarjeta.")
    await model.ainvoke([*messages, *g._for_model([
        answer, ToolMessage(content="Confirmado por la persona", tool_call_id="call_1"),
        HumanMessage("¿Qué sigue?"),
    ])])
    serialized = json.dumps(fake.last)
    assert "call_1" in serialized and "Confirmado por la persona" in serialized
    if fake.route == "openai":
        calls = [x for x in fake.last["input"] if x.get("type") == "function_call"]
        results = [x for x in fake.last["input"] if x.get("type") == "function_call_output"]
        assert len(calls) == len(results) == 1
        assert calls[0]["call_id"] == results[0]["call_id"] == "call_1"


@pytest.mark.parametrize("tool", [False, True])
async def test_streaming_aggregates_text_or_tool_and_usage(llm, tool):
    _, model, fake = build(llm)
    if tool:
        fake.tool("consultar_solicitud", {})
    else:
        fake.reply(text="Hola, cuéntame sobre tu auto.")
    chunks = [chunk async for chunk in model.astream([HumanMessage("hola")])]
    assert fake.last["stream"] is True
    full = chunks[0]
    for chunk in chunks[1:]:
        full += chunk
    assert full.usage_metadata["input_tokens"] == 1200
    assert full.usage_metadata["output_tokens"] == 300
    if tool:
        assert full.tool_calls[0]["name"] == "consultar_solicitud"
        assert full.tool_calls[0]["args"] == {}
        assert full.tool_calls[0]["id"] == "call_1"
    else:
        assert full.text == "Hola, cuéntame sobre tu auto."


@pytest.mark.parametrize("failure", ["refusal", "incomplete", "http"])
async def test_failed_or_refused_chat_settles_and_never_falls_back(llm, failure):
    g, model, fake = build(llm)
    if failure == "http":
        fake.fail(503).fail(503)  # el chat admite un reintento de transporte al mismo proveedor
    elif failure == "refusal":
        fake.reply(refusal=True)
    else:
        fake.tool("proponer_datos_del_auto", {"owned_by_customer": True})
        if fake.route == "openai":
            fake.replies[-1].update(status="incomplete",
                                    incomplete_details={"reason": "max_output_tokens"})
        else:
            fake.replies[-1]["stop_reason"] = "max_tokens"
    api = AsyncMock(return_value=(201, {"turn_id": "t1"}))
    with patch.object(g, "_llm", model), \
            patch.object(g, "_llm_meta", (fake.route, "modelo-snapshot")), \
            patch.object(g, "_api", api):
        result = await g.agent({"case_id": "case1", "messages": [HumanMessage("hola")]}, {})
    assert result["messages"][0].tool_calls == []
    expected = g.MODEL_REFUSED if failure == "refusal" else g.TURN_UNAVAILABLE
    assert result["messages"][0].content == expected
    assert len(fake.requests) == (2 if failure == "http" else 1)
    assert api.await_count == 2
    report = api.await_args.kwargs["body"]
    assert report["outcome"] == "ERROR" and report["provider"] == fake.route
    assert report["input_tokens"] == (0 if failure == "http" else 1200)
    assert result["model_route"] == {"provider": fake.route, "model": "modelo-snapshot"}


async def test_graph_stream_publishes_only_checked_customer_text(llm):
    g, model, fake = build(llm)
    fake.reply(text="READY_FOR_FINANCIAL. Te contactarán hoy; tu crédito está aprobado.")

    async def api(config, method, path, **kwargs):
        if path.endswith("/agent-turns"):
            return 201, {"turn_id": "t1"}
        if method == "GET":
            return 200, {"workflow_state": "READY_FOR_FINANCIAL"}
        return 200, {}

    with patch.object(g, "_llm", model), \
            patch.object(g, "_llm_meta", (fake.route, "modelo-snapshot")), \
            patch.object(g, "_api", api):
        chunks = [message async for message, metadata in g.graph.astream(
            {"case_id": "case1", "messages": [HumanMessage("¿Qué falta?")]},
            stream_mode="messages",
        ) if isinstance(message, AIMessage)]
    assert chunks, "SSE debe conservar el mensaje final revisado"
    visible = "".join(m.text for m in chunks)
    assert "READY_FOR_FINANCIAL" not in visible
    assert "contactarán" not in visible
    assert "tu crédito está aprobado" not in visible
    assert "no significa que el crédito esté aprobado" in visible
