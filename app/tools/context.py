from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.persistence.models import ActorRole


@dataclass(frozen=True)
class ExecutionContext:
    """Construido por el servidor tras autenticar; nunca a partir de argumentos del modelo."""

    actor_id: uuid.UUID
    role: ActorRole
    customer_id: uuid.UUID | None
    correlation_id: str
    delegated_for: uuid.UUID | None = None
