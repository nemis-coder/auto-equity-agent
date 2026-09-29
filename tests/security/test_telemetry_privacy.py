"""La exportación a Langfuse no debe contener PII (TDD §10, hallazgo H-06)."""

import json
import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability.context import parse_traceparent, trace_headers
from app.observability.settings import ModelPrice
from app.observability.traces import JsonlSink, Tracer, make_langfuse_client

PII_VALUES = [
    "Ana Prueba López",
    "Calle Demo",
    "tok_SECRETO_123",
    "Neto: $10,000.00 MXN",
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
]


@pytest.fixture
def exporter_and_tracer(tmp_path):
    exporter = InMemorySpanExporter()
    client = make_langfuse_client(
        public_key=f"pk-lf-test-{uuid.uuid4()}",
        secret_key="sk-lf-test",
        base_url="http://127.0.0.1:9",
        environment="test",
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    tracer = Tracer(sink=JsonlSink(tmp_path), langfuse_client=client)
    yield exporter, tracer, tmp_path
    client.shutdown()


def _all_exported_text(exporter: InMemorySpanExporter) -> str:
    chunks = []
    for span in exporter.get_finished_spans():
        chunks.append(span.name)
        chunks.extend(f"{k}={v}" for k, v in (span.attributes or {}).items())
    return "\n".join(chunks)


def test_exported_spans_contain_only_sanitized_metadata(exporter_and_tracer):
    exporter, tracer, trace_dir = exporter_and_tracer
    with tracer.span(
        "turn",
        kind="agent",
        metadata={
            "correlation_id": "c-1",
            "stage": "PROFILING",
            "full_name": PII_VALUES[0],
            "address": PII_VALUES[1],
            "token": PII_VALUES[2],
        },
    ) as span:
        with tracer.span("tool", kind="tool", metadata={"tool": "query_credit_bureau"}):
            pass
        span.update(metadata={"reason_code": PII_VALUES[3], "document": PII_VALUES[4]})
    tracer.flush()

    exported = _all_exported_text(exporter)
    assert "turn" in exported and "query_credit_bureau" in exported
    assert "PROFILING" in exported
    for value in PII_VALUES:
        assert value not in exported

    local = "".join(p.read_text() for p in trace_dir.glob("*.jsonl"))
    records = [json.loads(line) for line in local.splitlines()]
    assert {r["name"] for r in records} == {"turn", "tool"}
    for value in PII_VALUES:
        assert value not in local


def test_errors_propagate_and_are_recorded_without_detail(exporter_and_tracer):
    exporter, tracer, _ = exporter_and_tracer
    with pytest.raises(ValueError):
        with tracer.span("tool", kind="tool", metadata={"tool": "x"}):
            raise ValueError("Ana Prueba López tiene un adeudo")
    tracer.flush()
    exported = _all_exported_text(exporter)
    assert "ValueError" in exported
    assert "Ana Prueba López" not in exported


class _BrokenClient:
    def start_as_current_observation(self, **_):
        raise RuntimeError("langfuse caído")

    def flush(self):
        raise RuntimeError("langfuse caído")


def test_telemetry_failure_never_breaks_business_action(tmp_path):
    tracer = Tracer(sink=JsonlSink(tmp_path), langfuse_client=_BrokenClient())
    with tracer.span("tool", metadata={"tool": "x"}):
        result = 42
    tracer.flush()
    assert result == 42
    assert tracer.export_errors == 2
    assert list(tmp_path.glob("*.jsonl"))


def test_distributed_parentage_and_cost_reach_the_sdk(exporter_and_tracer):
    exporter, tracer, path = exporter_and_tracer
    tracer.model_prices = {"anthropic:test": ModelPrice(input=3, output=15)}
    trace_id = uuid.uuid4().hex
    with tracer.span("agent.tools", kind="tool", trace_context={"trace_id": trace_id}) as root:
        remote_context = parse_traceparent(trace_headers()["traceparent"])
        with tracer.span("http.request", trace_context=remote_context):
            with tracer.span("document.extraction", kind="generation", metadata={
                "provider": "anthropic", "model": "test",
            }) as generation:
                generation.update(usage={"input": 1000, "output": 100})
    tracer.flush()
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert len(spans) == 3
    assert all(f"{s.context.trace_id:032x}" == trace_id for s in spans.values())
    assert f"{spans['http.request'].parent.span_id:016x}" == root.span_id
    assert spans["document.extraction"].parent.span_id == spans["http.request"].context.span_id
    assert "0.0045" in _all_exported_text(exporter)
    local = [json.loads(line) for p in path.glob("*.jsonl") for line in p.read_text().splitlines()]
    assert {r["trace_id"] for r in local} == {trace_id}
    assert all(r["span_id"] == f"{spans[r['name']].context.span_id:016x}" for r in local)
