"""Instrumentación explícita del grafo: comparte el tracer, no reglas ni acceso a la BD."""

import atexit
import hashlib
import hmac
import uuid
from functools import lru_cache, wraps

from app.observability.context import current_span
from app.observability.settings import TelemetrySettings
from app.observability.traces import build_tracer


@lru_cache
def get_tracer():
    tracer = build_tracer(TelemetrySettings())
    atexit.register(tracer.flush)
    return tracer


def conversation_context(config: dict) -> dict[str, str]:
    """El mismo hilo/actor comparte trace_id, sin exportar IDs crudos ni el token.

    Es solo correlación: los permisos siguen verificándose en cada endpoint de la API.
    """
    cfg = config.get("configurable") or {}
    user = cfg.get("langgraph_auth_user")
    token = user.get("api_token", "") if isinstance(user, dict) else getattr(user, "api_token", "")
    thread = str(cfg.get("thread_id") or "")
    if not thread or not token:
        return {"trace_id": uuid.uuid4().hex}
    digest = hmac.new(token.encode(), thread.encode(), hashlib.sha256).hexdigest()[:32]
    return {"trace_id": digest}


def observe_node(name: str, *, kind: str = "chain"):
    """Mide trabajo activo. No envuelve interrupt(): la espera humana no es latencia de IA."""
    def decorate(fn):
        @wraps(fn)
        async def observed(state, config):
            metadata = {}
            if kind == "tool":
                calls = getattr(state["messages"][-1], "tool_calls", [])
                if calls:
                    metadata["tool"] = calls[0]["name"]
            context = None if current_span.get() else conversation_context(config)
            with get_tracer().span(name, kind=kind, metadata=metadata, trace_context=context):
                return await fn(state, config)
        return observed
    return decorate
