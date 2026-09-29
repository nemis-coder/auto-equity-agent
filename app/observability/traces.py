"""Trazas técnicas con spans explícitos.

No se usan callbacks ni wrappers automáticos: cada span recibe solo metadata sanitizada.
Langfuse es opcional en tiempo de ejecución (LANGFUSE_ENABLED); el respaldo JSONL local
siempre se escribe. Un fallo de telemetría nunca interrumpe la acción de negocio.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.context import current_span
from app.observability.costs import estimate_cost
from app.observability.sanitize import langfuse_mask, sanitize

log = logging.getLogger(__name__)

SpanKind = str  # "span" | "agent" | "tool" | "generation" | "chain"


class SpanHandle:
    def __init__(self, name: str, kind: SpanKind, metadata: dict[str, Any]) -> None:
        self.name = name
        self.kind = kind
        self.metadata = dict(metadata)
        self.level = "DEFAULT"
        self.usage: dict[str, int] | None = None
        self.cost_usd: float | None = None
        self._lf: Any = None
        self.trace_id = ""
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id: str | None = None

    def update(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        usage: dict[str, int] | None = None,
        cost_usd: float | None = None,
    ) -> None:
        if metadata:
            self.metadata.update(metadata)
        if level:
            self.level = level
        if usage is not None:
            self.usage = usage
        if cost_usd is not None:
            self.cost_usd = cost_usd


class JsonlSink:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"traces-{datetime.now(UTC):%Y%m%d}.jsonl"
            line = json.dumps(record, ensure_ascii=True, sort_keys=True)
            with self._lock, path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            log.warning("trace_sink_write_failed")


class Tracer:
    def __init__(self, *, sink: JsonlSink | None, langfuse_client: Any = None,
                 model_prices: dict | None = None) -> None:
        self._sink = sink
        self._lf = langfuse_client
        self.export_errors = 0
        self.model_prices = model_prices or {}

    @property
    def langfuse_enabled(self) -> bool:
        return self._lf is not None

    @contextmanager
    def span(
        self, name: str, *, kind: SpanKind = "span", metadata: dict[str, Any] | None = None,
        trace_context: dict[str, str] | None = None,
    ) -> Iterator[SpanHandle]:
        handle = SpanHandle(name, kind, sanitize(metadata or {}))
        parent = current_span.get()
        context = trace_context or (
            {"trace_id": parent.trace_id, "parent_span_id": parent.span_id} if parent else {}
        )
        handle.trace_id = context.get("trace_id") or uuid.uuid4().hex
        handle.parent_span_id = context.get("parent_span_id")
        started = time.monotonic()
        lf_cm = self._start_langfuse(handle)
        token = current_span.set(handle)
        error: BaseException | None = None
        try:
            yield handle
        except BaseException as exc:
            error = exc
            handle.update(level="ERROR", metadata={"error_code": type(exc).__name__})
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            handle.metadata["duration_ms"] = duration_ms
            if kind == "generation" and handle.cost_usd is None:
                handle.cost_usd, status = estimate_cost(
                    handle.metadata.get("provider", ""), handle.metadata.get("model", ""),
                    handle.usage, self.model_prices,
                )
                handle.metadata["cost_status"] = (
                    "partial_estimate" if status == "estimated" and handle.level == "ERROR"
                    else status
                )
            self._finish_langfuse(handle, lf_cm, error)
            current_span.reset(token)
            if self._sink is not None:
                self._sink.write(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "trace_id": handle.trace_id,
                        "span_id": handle.span_id,
                        "parent_span_id": handle.parent_span_id,
                        "name": handle.name,
                        "kind": handle.kind,
                        "level": handle.level,
                        "metadata": sanitize(handle.metadata),
                        "usage": handle.usage,
                        "cost_usd": handle.cost_usd,
                    }
                )

    def _start_langfuse(self, handle: SpanHandle) -> Any:
        if self._lf is None:
            return None
        try:
            cm = self._lf.start_as_current_observation(
                name=handle.name, as_type=handle.kind, metadata=handle.metadata,
                trace_context={"trace_id": handle.trace_id, **(
                    {"parent_span_id": handle.parent_span_id} if handle.parent_span_id else {}
                )},
            )
            handle._lf = cm.__enter__()
            handle.span_id = handle._lf.id
            return cm
        except Exception:  # noqa: BLE001 - la telemetría nunca rompe el negocio
            self.export_errors += 1
            log.warning("langfuse_span_start_failed")
            return None

    def _finish_langfuse(self, handle: SpanHandle, cm: Any, error: BaseException | None) -> None:
        if cm is None or handle._lf is None:
            return
        try:
            kwargs: dict[str, Any] = {
                "metadata": sanitize(handle.metadata),
                "level": handle.level,
            }
            if handle.usage is not None:
                # No contar dos veces tokens de caché en el total de Langfuse.
                kwargs["usage_details"] = dict(handle.usage)
                kwargs["usage_details"]["input"] -= (
                    handle.usage.get("cache_read", 0) + handle.usage.get("cache_creation", 0)
                )
            # Sin tarifa explícita no pedir a Langfuse que infiera precios por el alias.
            # El nombre del modelo siempre está disponible en metadata.
            if handle.kind == "generation" and handle.cost_usd is not None:
                kwargs["model"] = handle.metadata["model"]
            if handle.cost_usd is not None:
                kwargs["cost_details"] = {"total": handle.cost_usd}
            handle._lf.update(**kwargs)
        except Exception:  # noqa: BLE001
            self.export_errors += 1
            log.warning("langfuse_span_finish_failed")
        finally:
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                self.export_errors += 1
                log.warning("langfuse_span_exit_failed")

    def current_trace_id(self) -> str | None:
        if span := current_span.get():
            return span.trace_id
        if self._lf is None:
            return None
        try:
            return self._lf.get_current_trace_id()
        except Exception:  # noqa: BLE001
            return None

    def flush(self) -> None:
        if self._lf is None:
            return
        try:
            self._lf.flush()
        except Exception:  # noqa: BLE001
            self.export_errors += 1
            log.warning("langfuse_flush_failed")


def make_langfuse_client(
    *,
    public_key: str,
    secret_key: str,
    base_url: str,
    environment: str,
    span_exporter: Any = None,
    tracer_provider: Any = None,
) -> Any:
    from langfuse import Langfuse, is_langfuse_span

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
        mask=langfuse_mask,
        should_export_span=is_langfuse_span,
        span_exporter=span_exporter,
        tracer_provider=tracer_provider,
    )


def build_tracer(settings: Any) -> Tracer:
    sink = JsonlSink(Path(settings.trace_dir))
    client = None
    if settings.langfuse_enabled:
        client = make_langfuse_client(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
        )
    return Tracer(sink=sink, langfuse_client=client, model_prices=settings.model_prices)
