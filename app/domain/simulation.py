"""Ofertas del cotizador externo: validación y huella de lo que ve el cliente (decisión 0038).

La app no calcula montos ni cuotas: los entrega el cotizador de la financiera
(`app/providers/offers.py`). Aquí solo se comprueba, sin recalcular, que cada opción es
coherente con lo pedido antes de mostrarla. Una respuesta con cualquier opción incoherente se
rechaza completa (proveedor inválido), nunca se corrige en silencio.

Comprobaciones por opción:
- la llave se suma una sola vez y es exactamente la cotizada: capital = efectivo + llave;
- tasa igual a la del perfil y capital dentro del tope del perfil;
- el calendario es consistente: suma del capital pagado = capital, saldo final 0, cuotas iguales
  a las declaradas y total = suma de cuotas;
- ninguna cuota supera la capacidad de pago (política del caso).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")


class InvalidOffers(ValueError):
    """La respuesta del cotizador no es coherente; no se muestra ninguna opción."""


@dataclass(frozen=True)
class Offer:
    cash_amount: Decimal
    key_cost: Decimal
    financed_principal: Decimal
    annual_nominal_rate: Decimal
    term_months: int
    regular_payment: Decimal
    last_payment: Decimal
    total_payment: Decimal
    schedule: tuple[dict[str, str], ...]
    expires_at: datetime

    def display(self) -> dict[str, str | int]:
        """Contenido que ve el cliente; su hash prueba qué oferta eligió."""
        return {
            "cash_amount": str(self.cash_amount),
            "key_cost": str(self.key_cost),
            "financed_principal": str(self.financed_principal),
            "annual_nominal_rate": str(self.annual_nominal_rate),
            "term_months": self.term_months,
            "regular_payment": str(self.regular_payment),
            "last_payment": str(self.last_payment),
            "total_payment": str(self.total_payment),
            "expires_at": self.expires_at.isoformat(),
        }


def offer_hash(display: dict[str, str | int]) -> str:
    raw = json.dumps(display, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _money(value: Any) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(CENT):
        raise InvalidOffers("amount")
    return amount


def _positive_integer(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise InvalidOffers("integer")
    return int(value)


def schedule_consistent(
    schedule: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    principal: Decimal,
    term_months: int,
    regular_payment: Decimal,
    last_payment: Decimal,
    total_payment: Decimal,
) -> bool:
    """Verifica cada fila del proveedor, sin recalcular su tarifa ni corregir su respuesta."""
    try:
        if not schedule or len(schedule) != term_months:
            return False
        balance, total = principal, Decimal("0.00")
        for month, row in enumerate(schedule, start=1):
            payment = _money(row["payment"])
            paid, interest = _money(row["principal"]), _money(row["interest"])
            remaining = _money(row["balance"])
            expected_payment = last_payment if month == term_months else regular_payment
            if (
                _positive_integer(row["month"]) != month
                or payment != expected_payment
                or (month == 1 and payment != regular_payment)
                or payment != paid + interest
                or remaining != balance - paid
            ):
                return False
            balance, total = remaining, total + payment
        return balance == Decimal("0.00") and total == total_payment
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


def parse_offers(
    response: dict[str, Any],
    *,
    key_cost: Decimal,
    annual_rate: Decimal,
    max_financed_principal: Decimal,
    payment_capacity: Decimal,
    expires_at: datetime,
) -> tuple[Offer, ...]:
    """Valida la respuesta completa del cotizador; `expires_at` ya es el mínimo de vigencias."""
    if response.get("currency") != "MXN" or not isinstance(response.get("offers"), list):
        raise InvalidOffers("envelope")
    offers = []
    for raw in response["offers"]:
        try:
            cash = _money(raw["cash_amount"])
            key = _money(raw["key_cost"])
            principal = _money(raw["financed_principal"])
            rate = Decimal(str(raw["annual_nominal_rate"]))
            months = _positive_integer(raw["term_months"])
            regular = _money(raw["regular_payment"])
            last = _money(raw["last_payment"])
            total = _money(raw["total_payment"])
            schedule = tuple(raw["schedule"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise InvalidOffers("offer") from exc
        if not (
            cash > 0
            and months > 0
            and key == key_cost
            and principal == cash + key
            and rate.is_finite()
            and rate >= 0
            and rate == annual_rate
            and principal <= max_financed_principal
            and max(regular, last) <= payment_capacity
            and schedule_consistent(
                schedule,
                principal=principal,
                term_months=months,
                regular_payment=regular,
                last_payment=last,
                total_payment=total,
            )  # fmt: skip
        ):
            raise InvalidOffers("offer")
        offers.append(
            Offer(cash, key, principal, rate, months, regular, last, total, schedule, expires_at)
        )
    return tuple(sorted(offers, key=lambda o: (o.cash_amount, o.term_months)))
