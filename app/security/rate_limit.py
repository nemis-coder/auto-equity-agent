"""Límites por actor en memoria para la instancia única de demo (TDD §9.2).

Dos cupos independientes: comandos que modifican datos (S8: 30/min) y lecturas (más alto,
porque las pantallas y el agente releen el caso tras cada paso). Usa `time.monotonic()`, nunca el
reloj de negocio, para que CLOCK_MODE=fixed no congele la ventana (hallazgo H-04).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        write_limit: int,
        read_limit: int | None = None,
        window_seconds: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = {"write": write_limit, "read": read_limit or write_limit * 4}
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, kind: str = "write") -> bool:
        now = self._now()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limits[kind]:
                return False
            hits.append(now)
            return True
