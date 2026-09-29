"""Ledger durable de los proveedores simulados.

Simula al proveedor externo: sus efectos se registran en una transacción propia, separada
de la del caso, para poder probar respuestas perdidas, reinicios e idempotencia
(TDD §6.4) sin depender de la memoria del proceso.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.persistence.models import MockProviderLedger


class Ledger:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], provider: str) -> None:
        self._sm = sessionmaker
        self._provider = provider

    async def begin_attempt(self, key: str, req_hash: str) -> MockProviderLedger:
        async with self._sm() as session, session.begin():
            await session.execute(
                insert(MockProviderLedger)
                .values(
                    id=uuid.uuid4(),
                    provider=self._provider,
                    idempotency_key=key,
                    request_hash=req_hash,
                    attempts=0,
                    effects=0,
                )
                .on_conflict_do_nothing(constraint="uq_ledger_key")
            )
            row = await session.scalar(
                select(MockProviderLedger)
                .where(
                    MockProviderLedger.provider == self._provider,
                    MockProviderLedger.idempotency_key == key,
                )
                .with_for_update()
            )
            if row.request_hash != req_hash:
                raise ValueError("IDEMPOTENCY_KEY_REUSED_WITH_OTHER_REQUEST")
            row.attempts += 1
            await session.flush()
            session.expunge(row)
            return row

    async def record_effect(self, key: str, response: dict[str, Any]) -> None:
        async with self._sm() as session, session.begin():
            row = await session.scalar(
                select(MockProviderLedger)
                .where(
                    MockProviderLedger.provider == self._provider,
                    MockProviderLedger.idempotency_key == key,
                )
                .with_for_update()
            )
            if row.response is None:
                row.effects += 1
                row.response = response

    async def lookup(self, key: str) -> dict[str, Any] | None:
        async with self._sm() as session:
            return await session.scalar(
                select(MockProviderLedger.response).where(
                    MockProviderLedger.provider == self._provider,
                    MockProviderLedger.idempotency_key == key,
                )
            )
