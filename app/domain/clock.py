"""Reloj de negocio (TDD §11.1).

Solo `business_now()` se ve afectado por CLOCK_MODE=fixed. Rate limit, timeouts, leases y
duraciones usan siempre `time.monotonic()` y nunca este reloj.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def business_now(self) -> datetime: ...


class SystemClock:
    def business_now(self) -> datetime:
        return datetime.now(UTC)


class FixedAdvancingClock:
    """Empieza en `fixed_now` y avanza al ritmo del reloj real desde el arranque."""

    def __init__(self, fixed_now: datetime) -> None:
        if fixed_now.tzinfo is None:
            raise ValueError("FIXED_NOW debe incluir zona horaria")
        self._start = fixed_now.astimezone(UTC)
        self._t0 = time.monotonic()

    def business_now(self) -> datetime:
        return self._start + timedelta(seconds=time.monotonic() - self._t0)


class FakeClock:
    """Solo para pruebas en proceso: el tiempo de negocio avanza cuando la prueba lo pide."""

    def __init__(self, now: datetime) -> None:
        self._now = now.astimezone(UTC)

    def business_now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


def build_clock(mode: str, fixed_now: datetime | None) -> Clock:
    if mode == "fixed":
        if fixed_now is None:
            raise ValueError("CLOCK_MODE=fixed requiere FIXED_NOW")
        return FixedAdvancingClock(fixed_now)
    return SystemClock()
