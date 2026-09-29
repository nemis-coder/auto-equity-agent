"""Puerto de Buró de Crédito y su implementación mock (TDD §6.4, supuesto S1)."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base import ProviderResponseLost, ProviderTimeout, request_hash
from app.providers.mock_ledger import Ledger

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bureau" / "responses.json"


class BureauProvider(Protocol):
    name: str

    async def query(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None: ...


def _identity_key(full_name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", full_name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


class MockBureauProvider:
    """Buró simulado e idempotente por clave: repetir la clave devuelve el mismo efecto."""

    name = "mock_bureau"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._ledger = Ledger(sessionmaker, self.name)
        self._catalog = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def _fixture(self, full_name: str) -> dict[str, Any]:
        return self._catalog["identities"].get(_identity_key(full_name), self._catalog["default"])

    async def query(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        row = await self._ledger.begin_attempt(idempotency_key, request_hash(request))
        if row.response is not None:
            return row.response
        fixture = self._fixture(request["full_name"])
        behavior = fixture["behavior"]
        if behavior == "ALWAYS_TIMEOUT" or (behavior == "TIMEOUT_ONCE" and row.attempts == 1):
            raise ProviderTimeout("bureau_timeout")
        response = {
            **fixture["response"],
            "provider_ref": f"bureau-{idempotency_key[-16:]}",
        }
        await self._ledger.record_effect(idempotency_key, response)
        if behavior == "LOST_RESPONSE_ONCE" and row.attempts == 1:
            raise ProviderResponseLost("bureau_response_lost")
        return response

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        return await self._ledger.lookup(idempotency_key)
