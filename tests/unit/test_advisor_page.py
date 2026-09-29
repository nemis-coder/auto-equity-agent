"""Pantalla del asesor: se sirve con CSP estricta, sin recursos externos y con los textos del
backend embebidos como datos (no como código)."""

from __future__ import annotations

import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import texts
from app.api.advisor_page import PAGE_TEXTS, router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_page_embeds_the_backend_texts_as_json_data():
    r = _client().get("/asesor")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    raw = re.search(r'<script id="texts" type="application/json">(.*?)</script>', r.text, re.S)
    data = json.loads(raw.group(1))
    assert set(data) == set(PAGE_TEXTS)
    assert data["REVIEW_REASONS"] == texts.REVIEW_REASONS
    assert "__TEXTS__" not in r.text


def test_page_and_assets_carry_a_strict_security_policy():
    client = _client()
    for path in ("/asesor", "/asesor/asesor.js", "/asesor/asesor.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp
        assert "unsafe-inline" not in csp
        assert r.headers["x-content-type-options"] == "nosniff"


def test_page_loads_nothing_from_other_origins_and_only_known_assets_are_served():
    client = _client()
    page = client.get("/asesor").text
    assert not re.search(r'(src|href)="(https?:)?//', page)
    assert client.get("/asesor/main.py").status_code == 404
    assert client.get("/asesor/..%2Fmain.py").status_code == 404


def test_page_script_uses_text_nodes_not_html_injection():
    script = _client().get("/asesor/asesor.js").text
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
