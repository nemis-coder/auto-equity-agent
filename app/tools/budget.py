"""Presupuesto de tokens por caso (TDD §5.4 y supuesto S10).

Antes de cada llamada a un modelo se reserva el máximo por llamada con un `UPDATE` condicional
(atómico, sin mantener el bloqueo del caso durante la llamada). Al terminar se libera la
reserva y se suma el uso real. Si la reserva no cabe, la llamada no se hace: el caso se pausa
y pasa a un asesor; nunca se rechaza. Son estimaciones de aplicación, no facturación.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.persistence.models import Case

BUDGET_REASON = "TOKEN_BUDGET_EXCEEDED"


@dataclass(frozen=True)
class TokenBudget:
    case_input: int = 400_000
    case_output: int = 40_000
    call_input: int = 16_000
    call_output: int = 1_500

    @classmethod
    def from_settings(cls, settings) -> TokenBudget:
        return cls(
            case_input=settings.case_token_budget_input,
            case_output=settings.case_token_budget_output,
            call_input=settings.call_max_input_tokens,
            call_output=settings.call_max_output_tokens,
        )


@dataclass(frozen=True)
class Reservation:
    case_id: uuid.UUID
    input: int
    output: int


async def reserve(
    sessionmaker: async_sessionmaker[AsyncSession],
    case_id: uuid.UUID,
    budget: TokenBudget,
    calls: int = 1,
) -> Reservation | None:
    async with sessionmaker() as session, session.begin():
        return await reserve_in_session(session, case_id, budget, calls)


async def reserve_in_session(
    session: AsyncSession, case_id: uuid.UUID, budget: TokenBudget, calls: int = 1
) -> Reservation | None:
    """El llamador puede guardar la operación y su reserva en la misma transacción."""
    need_in, need_out = budget.call_input * calls, budget.call_output * calls
    row = await session.execute(
        update(Case)
        .where(
            Case.id == case_id,
            Case.tokens_in + Case.tokens_reserved_in + need_in
            <= budget.case_input * Case.token_allotments,
            Case.tokens_out + Case.tokens_reserved_out + need_out
            <= budget.case_output * Case.token_allotments,
        )
        .values(
            tokens_reserved_in=Case.tokens_reserved_in + need_in,
            tokens_reserved_out=Case.tokens_reserved_out + need_out,
        )
        .returning(Case.id)
    )
    return Reservation(case_id, need_in, need_out) if row.first() is not None else None


async def settle(
    sessionmaker: async_sessionmaker[AsyncSession],
    reservation: Reservation,
    used_input: int,
    used_output: int,
) -> None:
    """Libera la reserva y registra el uso real, aunque la llamada haya fallado."""
    async with sessionmaker() as session, session.begin():
        await settle_in_session(session, reservation, used_input, used_output)


async def settle_in_session(
    session: AsyncSession, reservation: Reservation, used_input: int, used_output: int
) -> None:
    """El dueño de la operación bloquea y termina su fila en esta misma transacción."""
    await session.execute(
        update(Case)
        .where(Case.id == reservation.case_id)
        .values(
            tokens_reserved_in=Case.tokens_reserved_in - reservation.input,
            tokens_reserved_out=Case.tokens_reserved_out - reservation.output,
            tokens_in=Case.tokens_in + max(used_input, 0),
            tokens_out=Case.tokens_out + max(used_output, 0),
        )
    )


async def pause_for_budget(sessionmaker, origin, case_id: uuid.UUID) -> None:
    """Sin presupuesto: se pausa y se escala a un asesor (no es un rechazo)."""
    from sqlalchemy import select

    from app.domain.states import TERMINAL_STATES, WaitReason, WorkflowState
    from app.tools.cases import bump
    from app.tools.credit import service_context
    from app.tools.reviews import open_review

    async with sessionmaker() as session, session.begin():
        case = await session.scalar(select(Case).where(Case.id == case_id).with_for_update())
        state = WorkflowState(case.workflow_state)
        if state in TERMINAL_STATES or state is WorkflowState.HUMAN_REVIEW:
            return
        svc_ctx = await service_context(session, origin)
        bump(case)
        await open_review(
            session, svc_ctx, case, reason_code=BUDGET_REASON, resume_state=state.value,
            details={"allotments": case.token_allotments}, wait=WaitReason.BUDGET_EXCEEDED,
        )  # fmt: skip
