"""Filtro de telemetría por lista blanca: nada sale de la aplicación si no está permitido."""

from __future__ import annotations

import re
from typing import Any

ALLOWED_KEYS = frozenset(
    {
        "correlation_id",
        "case_alias",
        "stage",
        "action",
        "tool",
        "document_kind",
        "reason_code",
        "status",
        "outcome",
        "operation_id",
        "model",
        "provider",
        "agent_version",
        "prompt_version",
        "policy_version",
        "tool_version",
        "schema_version",
        "duration_ms",
        "attempt",
        "scenario",
        "error_code",
        "mode",
        "role",
        "http_status",
        "route",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_status",
        "trace_id",
        "span_id",
        "parent_span_id",
    }
)

MAX_STRING = 120
REDACTED = "[redacted]"
_SAFE_STRING = re.compile(rf"^[A-Za-z0-9_.:/\-]{{0,{MAX_STRING}}}$")


def _clean_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str) and _SAFE_STRING.fullmatch(value):
        return value
    return REDACTED


def sanitize(data: Any) -> Any:
    """Devuelve solo claves permitidas con escalares seguros.

    Los strings se admiten únicamente como identificadores (sin espacios ni tildes),
    lo que excluye nombres, domicilios, texto libre de documentos y base64 largo.
    """
    if isinstance(data, dict):
        return {
            str(k): _clean_scalar(v)
            for k, v in data.items()
            if str(k) in ALLOWED_KEYS and not isinstance(v, dict | list | tuple)
        }
    if data is None or isinstance(data, bool | int | float):
        return data
    return REDACTED


def langfuse_mask(*, data: Any, **_: Any) -> Any:
    return sanitize(data)
