"""Envía una traza de prueba sanitizada a Langfuse (perfil demo integrada).

Comprueba recepción por lectura; flush() no es una confirmación del servidor.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.observability.traces import build_tracer  # noqa: E402


def verify_receipt(client, trace_id: str, *, attempts: int = 10, delay: float = 2) -> bool:
    """Espera acotada por consistencia eventual; no imprime respuestas ni credenciales."""
    started = datetime.now(UTC) - timedelta(minutes=5)
    for attempt in range(attempts):
        try:
            observations = client.api.observations.get_many(
                trace_id=trace_id, fields="core,basic", limit=10,
                from_start_time=started, to_start_time=datetime.now(UTC) + timedelta(minutes=1),
                request_options={"timeout_in_seconds": 5, "max_retries": 0},
            )
            names = {o.name for o in observations.data}
            if {"smoke.turn", "smoke.tool"} <= names:
                return True
        except Exception:  # noqa: BLE001, S110 - no imprimir respuesta remota ni credenciales
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def main() -> int:
    settings = get_settings()
    if not settings.langfuse_enabled:
        print("LANGFUSE_ENABLED=false: no se envió traza (perfil evaluador).")
        return 2
    tracer = build_tracer(settings)
    client = tracer._lf  # noqa: SLF001 - verificación explícita de credenciales
    if not client.auth_check():
        print("Langfuse rechazó las credenciales.")
        return 1
    correlation_id = str(uuid.uuid4())
    with tracer.span(
        "smoke.turn",
        kind="agent",
        metadata={"correlation_id": correlation_id, "scenario": "h1_smoke", "stage": "NONE"},
    ):
        with tracer.span("smoke.tool", kind="tool", metadata={"tool": "get_case_context"}):
            trace_id = tracer.current_trace_id()
    tracer.flush()
    if tracer.export_errors or not trace_id or not verify_receipt(client, trace_id):
        print(f"Recepción NO confirmada. trace_id={trace_id}; revisa red, permisos y Langfuse.")
        return 1
    print(f"Recepción confirmada. trace_id={trace_id} correlation_id={correlation_id}")
    url = client.get_trace_url(trace_id=trace_id) if trace_id else None
    if url:
        print(f"URL: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
