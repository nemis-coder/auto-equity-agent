"""Puerto del cotizador de ofertas de la financiera y su mock (decisión 0038).

Las opciones de crédito (montos, plazos, cuotas y calendario) las entrega un sistema externo,
como el cotizador de la financiera. La app no las calcula: pide las opciones con las condiciones
del perfil (tasa y tope de capital), el ingreso neto mensual y el costo de la segunda llave, y
valida la respuesta antes de mostrarla (`app/domain/simulation.py`).

El mock es determinístico: amortización francesa con tasa nominal anual fija y la llave sumada
una sola vez al capital. Ofrece los montos de su tarifa que caben en el tope de capital y en la
capacidad de pago.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base import request_hash
from app.providers.mock_ledger import Ledger

TARIFF = Path(__file__).resolve().parents[2] / "fixtures" / "offers" / "tariff.json"
CENT = Decimal("0.01")


class OfferProvider(Protocol):
    name: str

    async def quote_offers(
        self, request: dict[str, Any], idempotency_key: str, now: datetime
    ) -> dict[str, Any]: ...

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None: ...


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def amortize(principal: Decimal, annual_rate: Decimal, months: int) -> list[dict[str, str]]:
    """Calendario mensual con cuota fija; la última cuota ajusta el redondeo."""
    with localcontext() as ctx:
        ctx.prec = 28
        monthly = annual_rate / Decimal(12)
        raw = (
            principal / months
            if monthly == 0
            else principal * monthly / (1 - (1 + monthly) ** (-months))
        )
        regular, balance, rows = _q(raw), principal, []
        for month in range(1, months + 1):
            interest = _q(balance * monthly)
            payment = _q(balance + interest) if month == months else regular
            paid = payment - interest
            balance = _q(balance - paid)
            rows.append({"month": month, "payment": str(payment), "interest": str(interest),
                         "principal": str(paid), "balance": str(balance)})  # fmt: skip
    return rows


class MockOfferProvider:
    name = "mock_offers"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._ledger = Ledger(sessionmaker, self.name)
        self._tariff = json.loads(TARIFF.read_text(encoding="utf-8"))

    async def quote_offers(
        self, request: dict[str, Any], idempotency_key: str, now: datetime
    ) -> dict[str, Any]:
        row = await self._ledger.begin_attempt(idempotency_key, request_hash(request))
        if row.response is not None:
            return row.response
        response = {
            "quote_ref": f"offers-{idempotency_key[-16:]}",
            "provider_ref": "offers-provider-mock",
            "currency": "MXN",
            "valid_until": (now + timedelta(days=self._tariff["validity_days"])).isoformat(),
            "offers": self._offers(request),
        }
        await self._ledger.record_effect(idempotency_key, response)
        return response

    def _offers(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        rate = Decimal(request["annual_nominal_rate"])
        cap = Decimal(request["max_financed_principal"])
        key = Decimal(request["key_cost"])
        capacity = Decimal(request["monthly_net_income"]) * Decimal(
            self._tariff["max_payment_to_income_ratio"]
        )
        offers = []
        for amount in self._tariff["cash_amounts"]:
            cash = Decimal(amount)
            principal = cash + key
            if principal > cap:
                continue
            for months in self._tariff["terms_months"]:
                schedule = amortize(principal, rate, months)
                payments = [Decimal(r["payment"]) for r in schedule]
                if max(payments) > capacity:
                    continue
                offers.append({
                    "cash_amount": str(cash), "key_cost": str(key),
                    "financed_principal": str(principal), "annual_nominal_rate": str(rate),
                    "term_months": months, "regular_payment": str(payments[0]),
                    "last_payment": str(payments[-1]),
                    "total_payment": str(sum(payments, Decimal("0.00"))), "schedule": schedule,
                })  # fmt: skip
        return offers

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        return await self._ledger.lookup(idempotency_key)
