"""Pantalla web del asesor (decisión 0036): HTML y JS estáticos servidos por la misma API.

La página no tiene lógica de negocio ni credenciales propias: el asesor escribe su token y el
navegador llama a los mismos endpoints que cualquier cliente de la API (`/reviews`, `/cases`,
resoluciones). Los textos salen de `app.texts`, embebidos como JSON en la página.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app import texts

router = APIRouter(include_in_schema=False)
STATIC = Path(__file__).parent / "static"
# Solo recursos del propio origen: el JSON de textos no se ejecuta y el JS va en su archivo.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' blob: data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
PAGE_TEXTS = (
    "STAGES", "STATES", "REJECTIONS", "FIELDS", "EMPLOYMENT", "PERIODS", "ERRORS",
    "REVIEW_REASONS", "RESOLUTIONS", "CORRECTION_TARGETS", "OPERATIONS", "SLOTS",
    "DOCUMENT_KINDS", "DOCUMENT_STATUS", "DOCUMENT_FIELDS", "CORRECTIONS",
)  # fmt: skip


@cache
def _page() -> str:
    data = json.dumps({name: getattr(texts, name) for name in PAGE_TEXTS}, ensure_ascii=False)
    # `</` dentro de un <script> cerraría la etiqueta: se escapa aunque hoy ningún texto lo use.
    data = data.replace("</", "<\\/")
    return (STATIC / "asesor.html").read_text(encoding="utf-8").replace("__TEXTS__", data)


@router.get("/asesor")
async def advisor_page() -> HTMLResponse:
    return HTMLResponse(_page(), headers=SECURITY_HEADERS)


@router.get("/asesor/{name}")
async def advisor_asset(name: str) -> FileResponse:
    if name not in ("asesor.js", "asesor.css"):
        raise HTTPException(status_code=404)
    return FileResponse(STATIC / name, headers=SECURITY_HEADERS)
