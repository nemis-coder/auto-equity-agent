from __future__ import annotations

import hashlib
import json
from typing import Any


class ProviderError(Exception):
    """Fallo del proveedor. Nunca se traduce en aprobación ni rechazo del cliente."""

    retryable: bool = False
    effect_may_have_happened: bool = False


class ProviderTimeout(ProviderError):
    """Timeout sin efecto confirmado; el reintento con la misma clave es seguro."""

    retryable = True


class ProviderResponseLost(ProviderError):
    """El proveedor procesó la solicitud, pero la respuesta no llegó (TDD §6.9 paso 5)."""

    retryable = True
    effect_may_have_happened = True


def request_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
