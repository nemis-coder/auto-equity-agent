"""Contexto por tarea async y propagación W3C, también cuando Langfuse está desactivado."""

import re
from contextvars import ContextVar
from typing import Any

current_span: ContextVar[Any] = ContextVar("telemetry_span", default=None)
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-0[01]$")


def parse_traceparent(value: str) -> dict[str, str] | None:
    match = _TRACEPARENT.fullmatch(value)
    if not match or not int(match[1], 16) or not int(match[2], 16):
        return None
    return {"trace_id": match[1], "parent_span_id": match[2]}


def trace_headers() -> dict[str, str]:
    span = current_span.get()
    if span is None:
        return {}
    return {"traceparent": f"00-{span.trace_id}-{span.span_id}-01"}
