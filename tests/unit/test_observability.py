"""Correlación, aislamiento async y costos: sin modelo ni exportación a servicios reales."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.observability.agent import conversation_context
from app.observability.context import current_span, parse_traceparent, trace_headers
from app.observability.costs import estimate_cost, token_usage
from app.observability.settings import ModelPrice, TelemetrySettings
from app.observability.traces import JsonlSink, Tracer, build_tracer


def records(path):
    return [json.loads(line) for p in path.glob("*.jsonl") for line in p.read_text().splitlines()]


def test_cost_is_estimated_from_exact_model_and_cache_rates():
    prices = {"anthropic:test": ModelPrice(input=3, output=15, cache_read=0.3, cache_creation=3.75)}
    usage = {"input": 1000, "output": 100, "cache_read": 200, "cache_creation": 100}
    cost, status = estimate_cost("anthropic", "test", usage, prices)
    assert cost == pytest.approx(0.004035) and status == "estimated"
    assert estimate_cost("anthropic", "other", usage, prices) == (None, "rate_missing")
    assert estimate_cost("anthropic", "test", None, prices) == (None, "usage_unknown")
    assert estimate_cost("fake", "fixture", usage, {}) == (0, "simulated")


def test_unknown_cache_rate_is_not_a_free_or_full_price_token():
    prices = {"anthropic:test": ModelPrice(input=3, output=15)}
    assert estimate_cost("anthropic", "test", {"input": 100, "cache_read": 50}, prices) == (
        None, "cache_rate_missing",
    )
    assert estimate_cost("anthropic", "test", {"input": 10, "cache_read": 50}, prices) == (
        None, "usage_invalid",
    )


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_rates_are_rejected(value):
    with pytest.raises(ValidationError):
        ModelPrice(input=value, output=1)


def test_prices_load_from_environment_without_business_settings(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES", '{"anthropic:test":{"input":3,"output":15}}')
    assert TelemetrySettings().model_prices["anthropic:test"].input == 3


@pytest.mark.parametrize("name", ["agent.turn", "document.extraction"])
@pytest.mark.parametrize("cached,expected", [(0, 0.00056), (800, 0.00032)])
def test_example_prices_produce_costed_generations(monkeypatch, tmp_path, name, cached, expected):
    """La configuración entregada estima ambas funciones sin red ni doble cobro de caché."""
    monkeypatch.delenv("MODEL_PRICES", raising=False)
    settings = TelemetrySettings(
        _env_file=Path(__file__).resolve().parents[2] / ".env.example",
        trace_dir=tmp_path,
        langfuse_enabled=False,
    )
    tracer = build_tracer(settings)
    with tracer.span(name, kind="generation", metadata={
        "provider": "openai", "model": "gpt-4.1-mini",
    }) as span:
        span.update(usage=token_usage({
            "input_tokens": 1000, "output_tokens": 100,
            "input_token_details": {"cache_read": cached},
        }))
    record, = records(tmp_path)
    assert record["metadata"]["cost_status"] == "estimated"
    assert record["cost_usd"] == pytest.approx(expected)
    # La tarifa de ejemplo no se aplica silenciosamente a otro modelo/proveedor.
    assert estimate_cost("anthropic", "gpt-4.1-mini", record["usage"], settings.model_prices) == (
        None, "rate_missing",
    )
    assert estimate_cost("openai", "other", record["usage"], settings.model_prices) == (
        None, "rate_missing",
    )


def test_usage_keeps_cache_as_a_subset_of_input():
    assert token_usage({"input_tokens": 100, "output_tokens": 20,
                        "input_token_details": {"cache_read": 30}}) == {
        "input": 100, "output": 20, "cache_read": 30,
    }


@pytest.mark.parametrize("header", ["", "garbage", "00-" + "0" * 32 + "-" + "1" * 16 + "-01",
                                    "00-" + "1" * 32 + "-" + "0" * 16 + "-01"])
def test_invalid_context_is_ignored(header):
    assert parse_traceparent(header) is None


def test_conversation_context_is_stable_private_and_scoped_to_actor():
    config = {"configurable": {"thread_id": "private-thread", "langgraph_auth_user": {
        "api_token": "secret-a",
    }}}
    first = conversation_context(config)
    assert first == conversation_context(config)
    config["configurable"]["langgraph_auth_user"]["api_token"] = "secret-b"
    assert first != conversation_context(config)
    assert len(first["trace_id"]) == 32
    assert "secret" not in str(first) and "private-thread" not in str(first)


async def test_concurrent_traces_do_not_leak_context(tmp_path):
    tracer = Tracer(sink=JsonlSink(tmp_path))

    async def run(trace_id):
        with tracer.span("root", trace_context={"trace_id": trace_id}) as root:
            await asyncio.sleep(0)
            with tracer.span("child") as child:
                assert child.trace_id == trace_id and child.parent_span_id == root.span_id
                assert parse_traceparent(trace_headers()["traceparent"])["trace_id"] == trace_id
        assert current_span.get() is None

    await asyncio.gather(run("1" * 32), run("2" * 32))
    assert len(records(tmp_path)) == 4
    assert trace_headers() == {}


def test_generation_cost_and_failures_are_not_faked_as_zero(tmp_path):
    tracer = Tracer(sink=JsonlSink(tmp_path), model_prices={
        "anthropic:test": ModelPrice(input=3, output=15),
    })
    with tracer.span("model", kind="generation", metadata={
        "provider": "anthropic", "model": "test",
    }) as span:
        span.update(usage={"input": 1000, "output": 100})
    with pytest.raises(RuntimeError), tracer.span("failure", kind="generation"):
        raise RuntimeError("private contents")
    good, bad = records(tmp_path)
    assert good["cost_usd"] == pytest.approx(0.0045)
    assert good["metadata"]["cost_status"] == "estimated"
    assert bad["cost_usd"] is None and bad["metadata"]["cost_status"] == "usage_unknown"
    assert bad["level"] == "ERROR" and "private contents" not in str(bad)
    assert current_span.get() is None
