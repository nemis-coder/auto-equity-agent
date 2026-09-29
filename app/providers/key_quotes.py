"""Puerto de cotización de segunda llave y su mock (TDD §6.4, supuesto S2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base import ProviderTimeout, request_hash
from app.providers.mock_ledger import Ledger

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "key_quotes" / "quotes.json"


class KeyQuoteProvider(Protocol):
    name: str

    async def quote(
        self, request: dict[str, Any], idempotency_key: str, now: datetime
    ) -> dict[str, Any]: ...

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None: ...


class MockKeyQuoteProvider:
    name = "mock_key_quote"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._ledger = Ledger(sessionmaker, self.name)
        self._catalog = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def _fixture(self, vehicle_ref: str) -> dict[str, Any]:
        for prefix, fixture in self._catalog["by_vehicle_ref_prefix"].items():
            if vehicle_ref.startswith(prefix):
                return fixture
        return self._catalog["default"]

    async def quote(
        self, request: dict[str, Any], idempotency_key: str, now: datetime
    ) -> dict[str, Any]:
        row = await self._ledger.begin_attempt(idempotency_key, request_hash(request))
        if row.response is not None:
            return row.response
        fixture = self._fixture(request["vehicle_ref"])
        if fixture["behavior"] == "TIMEOUT_ONCE" and row.attempts == 1:
            raise ProviderTimeout("key_quote_timeout")
        response = {
            "quote_id": f"key-{idempotency_key[-16:]}",
            "provider_ref": "key-provider-mock",
            "vehicle_ref": request["vehicle_ref"],
            "amount": fixture["amount"],
            "currency": fixture["currency"],
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=fixture["validity_days"])).isoformat(),
        }
        await self._ledger.record_effect(idempotency_key, response)
        return response

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        return await self._ledger.lookup(idempotency_key)
