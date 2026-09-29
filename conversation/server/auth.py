"""Autenticación de Aegra con los tokens de demo de Auto Equity (decisión 0035).

Agent Chat UI envía lo que el usuario escribe en «LangSmith API Key» como cabecera `X-Api-Key`;
aquí se usa ese campo para el token del cliente. El token se valida contra `GET /me` de la API
de Auto Equity: Aegra no guarda usuarios propios y aísla los hilos por `identity`.
"""

from __future__ import annotations

import logging
import os

import httpx
from langgraph_sdk import Auth

API = os.environ.get("AUTO_EQUITY_API_URL", "http://api:8000")

auth = Auth()
log = logging.getLogger("conversation.auth")


def _token(headers: dict) -> str:
    lowered = {str(k).lower(): v for k, v in headers.items()}
    raw = lowered.get("x-api-key") or lowered.get("authorization") or b""
    value = raw.decode() if isinstance(raw, bytes) else str(raw)
    # Tolera lo que suele colarse al pegar: espacios, comillas y el prefijo «Bearer».
    return value.strip().removeprefix("Bearer ").strip().strip("\"'«»“”").strip()


@auth.authenticate
async def authenticate(headers: dict) -> dict:
    token = _token(headers)
    if not token:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Falta el token de demo.")
    async with httpx.AsyncClient(base_url=API, timeout=10) as client:
        response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        # Diagnóstico sin exponer el token: solo longitud y primeros 4 caracteres.
        log.warning("token_rechazado len=%s prefijo=%s", len(token), token[:4])
        raise Auth.exceptions.HTTPException(status_code=401, detail="Token inválido.")
    me = response.json()
    if me.get("role") != "CUSTOMER":
        raise Auth.exceptions.HTTPException(status_code=403, detail="Solo clientes.")
    # `api_token` viaja en la configuración de cada corrida para que las herramientas llamen a
    # la API como el cliente. Limitación de la prueba: ver README.
    return {"identity": me["actor_id"], "display_name": me["alias"], "api_token": token}
