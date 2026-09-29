"""Lease de turnos `AGENT_TURN` para `POST /cases/{id}/runs` (TDD §6.9, «Turnos interrumpidos»).

`AGENT_TURN` es el nombre persistido del avance determinístico del expediente. Las llamadas
del grafo al modelo se registran por separado como `CHAT_TURN` mediante `/agent-turns`.
Un turno nunca queda `UNKNOWN`: si el proceso muere, el lease vence y un replay con la misma
clave lo reejecuta desde el estado durable. Solo hay un turno activo por caso.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import Operation
from app.tools.cases import input_hash
from app.tools.context import ExecutionContext

AGENT_TURN = "AGENT_TURN"


def new_turn(
    ctx: ExecutionContext, case_id: uuid.UUID, key: str, text: str, lease_until: datetime
) -> Operation:
    return Operation(
        id=uuid.uuid4(),
        actor_id=ctx.actor_id,
        case_id=case_id,
        action=AGENT_TURN,
        idempotency_key=key,
        input_hash=input_hash({"text": text}),
        status="PENDING",
        attempts=1,
        lease_expires_at=lease_until,
    )


async def turn_operation(
    session: AsyncSession, ctx: ExecutionContext, key: str
) -> Operation | None:
    return await session.scalar(
        select(Operation)
        .where(Operation.actor_id == ctx.actor_id, Operation.idempotency_key == key)
        .with_for_update()
    )


async def active_turn(session: AsyncSession, case_id: uuid.UUID, real_now: datetime) -> bool:
    """Un solo turno a la vez por caso; el lease usa tiempo técnico, no el reloj de negocio."""
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(Operation)
            .where(
                Operation.case_id == case_id,
                Operation.action == AGENT_TURN,
                Operation.status == "PENDING",
                Operation.lease_expires_at > real_now,
            )
        )
    )
