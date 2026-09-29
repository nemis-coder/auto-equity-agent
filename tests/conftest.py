"""Configuración común de pytest.

Las pruebas son herméticas: aunque el `.env` tenga claves reales, nunca llaman a un proveedor
de IA ni envían trazas a Langfuse. `Settings` completa con el entorno lo que no recibe, así que
aquí se neutralizan esas variables antes de recolectar; las pruebas que necesitan un proveedor
lo pasan de forma explícita y la red se simula.

`tests/e2e_ui` necesita el stack levantado y un navegador: solo se recolecta cuando se pide de
forma explícita (`pytest tests/e2e_ui`). Así `pytest tests` dentro del contenedor de la API no
lo intenta; y cuando se pide, falla si falta configuración en lugar de omitirse en silencio.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

E2E = Path(__file__).parent / "e2e_ui"

EXTERNAL_SERVICE_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "LLM_GATEWAY_URL",
    "LLM_GATEWAY_API_KEY",
    "EXTRACTION_MODEL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)


def pytest_configure(config) -> None:
    traces = tempfile.TemporaryDirectory(prefix="auto-equity-test-traces-")
    config.add_cleanup(traces.cleanup)
    for name in EXTERNAL_SERVICE_VARS:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "APP_MODE": "demo",
            "EXTRACTION_PROVIDER": "fake",
            "LANGFUSE_ENABLED": "false",
            "TRACE_DIR": traces.name,
            "MODEL_PRICES": "{}",
        }
    )


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:
    if E2E in (collection_path, *collection_path.parents):
        requested = any("e2e_ui" in str(arg) for arg in config.args)
        return None if requested else True
    return None
