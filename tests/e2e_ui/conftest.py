"""Pruebas de navegador contra el stack en marcha: pantalla del asesor → API → PostgreSQL/MinIO.

La conversación del cliente (Agent Chat UI) no se prueba aquí: necesita un modelo real. Sus
reglas se prueban en `conversation/server/test_graph.py` y la API, en las pruebas de
integración. Aquí el cliente avanza por la API y el asesor trabaja en el navegador.

Variables:
- `E2E_API_URL` (por defecto http://127.0.0.1:8000); la pantalla está en `<API>/asesor`.
- `E2E_CREDENTIALS`: ruta al JSON de credenciales demo
  (`docker compose exec api cat /data/demo-credentials.json > /tmp/creds.json`).
- `E2E_CHROMIUM` (opcional): ejecutable de Chromium si no se usa el de Playwright.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

API = os.environ.get("E2E_API_URL", "http://127.0.0.1:8000")
ADVISOR = f"{API}/asesor"


@pytest.fixture(scope="session")
def creds() -> dict[str, str]:
    path = os.environ.get("E2E_CREDENTIALS")
    if not path or not Path(path).exists():
        pytest.fail("Falta E2E_CREDENTIALS con las credenciales demo.", pytrace=False)
    try:
        urllib.request.urlopen(f"{API}/readyz", timeout=5)  # noqa: S310 - URL local configurada
    except OSError as exc:
        pytest.fail(f"El stack no responde ({exc}); levanta docker compose.", pytrace=False)
    return json.loads(Path(path).read_text())


@pytest.fixture(scope="session")
def playwright():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright):
    executable = os.environ.get("E2E_CHROMIUM")
    b = (
        playwright.chromium.launch(executable_path=executable)
        if executable
        else playwright.chromium.launch()
    )
    yield b
    b.close()


@pytest.fixture(scope="session")
def http(playwright):
    """Cliente HTTP de Playwright para que el cliente avance por la API (sin dependencias)."""
    ctx = playwright.request.new_context(base_url=API)
    yield ctx
    ctx.dispose()


@pytest.fixture
def new_page(browser):
    contexts = []

    def make(width: int = 1400, height: int = 1300):
        viewport = {"width": width, "height": height}
        ctx = browser.new_context(viewport=viewport, accept_downloads=True)
        contexts.append(ctx)
        return ctx.new_page()

    yield make
    for ctx in contexts:
        ctx.close()
