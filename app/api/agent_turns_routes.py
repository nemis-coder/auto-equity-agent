"""Turnos del agente de la conversación: presupuesto por caso y traza (TDD §5.4 y §8.3).

El agente (Aegra) no guarda nada propio: antes de cada llamada al modelo pide un turno aquí,
que cuenta contra el límite de turnos del caso y reserva tokens del presupuesto persistente
(el mismo que usa la lectura de documentos). Al terminar informa el uso real, que se liquida y
se audita como `agent.usage` sin texto. `agent.turn` se mide en el agente (0041).
Sin presupuesto, el caso pasa a un asesor: nunca se
rechaza.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import execution_context, idempotency_key, services
from app.domain.states import TERMINAL_STATES, WaitReason, WorkflowState
from app.persistence.models import ActorRole, HumanReview, Operation
from app.tools.budget import Reservation, TokenBudget, pause_for_budget
from app.tools.budget import reserve_in_session as reserve
from app.tools.budget import settle_in_session as settle
from app.tools.cases import CHAT_TURN, bump, input_hash, load_case, require_role
from app.tools.context import ExecutionContext
from app.tools.credit import service_context
from app.tools.errors import ActionError, not_found
from app.tools.reviews import open_review

router = APIRouter()
Ctx = Annotated[ExecutionContext, Depends(execution_context)]
TURN_BUDGET_REASON = "TURN_BUDGET_EXCEEDED"
# Una reserva sin liquidar (el agente cayó a mitad de la llamada) se libera pasado este tiempo.
STALE_AFTER = timedelta(minutes=10)


class UsageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=128)
    outcome: Literal["OK", "ERROR"]


async def _turns_since_budget_review(session, case_id: uuid.UUID) -> int:
    """Turnos del agente desde la última revisión de presupuesto resuelta."""
    since = await session.scalar(
        select(func.max(HumanReview.resolved_at)).where(
            HumanReview.case_id == case_id,
            HumanReview.reason_code == TURN_BUDGET_REASON,
            HumanReview.status == "RESOLVED",
        )
    )
    stmt = (
        select(func.count())
        .select_from(Operation)
        .where(Operation.case_id == case_id, Operation.action == CHAT_TURN)
    )
    if since is not None:
        stmt = stmt.where(Operation.created_at > since)
    return await session.scalar(stmt)


async def _release_stale(session, case_id: uuid.UUID, now: datetime) -> None:
    """Con el caso bloqueado: estado y devolución de tokens se confirman juntos."""
    stale = await session.scalars(
        select(Operation)
        .where(
            Operation.case_id == case_id,
            Operation.action == CHAT_TURN,
            Operation.status == "PENDING",
            Operation.created_at < now - STALE_AFTER,
        )
        .with_for_update()
    )
    for op in stale:
        op.status, op.updated_at = "FAILED_FINAL", now
        await settle(session, _reservation(op), 0, 0)


def _reservation(op: Operation) -> Reservation:
    return Reservation(op.case_id, op.result["reserved_in"], op.result["reserved_out"])


def _exhausted(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "code": code,
            "message": "Se agotó el presupuesto automático; un asesor revisará la solicitud.",
            "retryable": False,
        },
    )


@router.post("/cases/{case_id}/agent-turns")
async def start_turn(case_id: uuid.UUID, request: Request, ctx: Ctx) -> JSONResponse:
    key = idempotency_key(request)
    svc = services(request)
    settings = svc.settings
    now = datetime.now(UTC)
    op_key = f"chat:{key}"
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)
        existing = await session.scalar(
            select(Operation).where(
                Operation.actor_id == ctx.actor_id, Operation.idempotency_key == op_key
            )
        )
        if existing is not None:
            if existing.case_id != case.id:
                raise ActionError(409, "IDEMPOTENCY_CONFLICT", "La clave ya se usó con otro caso.")
            replay = {"Idempotent-Replay": "true"}
            return JSONResponse({"turn_id": str(existing.id)}, headers=replay)
        await _release_stale(session, case_id, now)
        if await _turns_since_budget_review(session, case.id) >= settings.max_turns_per_case:
            state = WorkflowState(case.workflow_state)
            if state not in TERMINAL_STATES and state is not WorkflowState.HUMAN_REVIEW:
                svc_ctx = await service_context(session, ctx)
                previous = case.workflow_state
                bump(case)
                await open_review(
                    session, svc_ctx, case, reason_code=TURN_BUDGET_REASON,
                    resume_state=previous, wait=WaitReason.BUDGET_EXCEEDED,
                )  # fmt: skip
            return _exhausted(TURN_BUDGET_REASON)

        # El bloqueo cubre conteo, reserva e inserción. Un error revierte los tres.
        reservation = await reserve(session, case_id, TokenBudget.from_settings(settings))
        if reservation is not None:
            turn = Operation(
                id=uuid.uuid4(), actor_id=ctx.actor_id, case_id=case_id, action=CHAT_TURN,
                idempotency_key=op_key, input_hash=input_hash({}), status="PENDING",
                result={"reserved_in": reservation.input, "reserved_out": reservation.output},
            )
            session.add(turn)
    if reservation is None:
        await pause_for_budget(svc.sessionmaker, ctx, case_id)
        return _exhausted("TOKEN_BUDGET_EXCEEDED")
    return JSONResponse({"turn_id": str(turn.id)}, status_code=201)


@router.post("/cases/{case_id}/agent-turns/{turn_id}/usage")
async def report_usage(
    case_id: uuid.UUID, turn_id: uuid.UUID, payload: UsageIn, request: Request, ctx: Ctx
) -> dict[str, Any]:
    svc = services(request)
    now = datetime.now(UTC)
    async with svc.sessionmaker() as session, session.begin():
        # Orden único de bloqueos: caso → operación, igual que creación y expiración.
        await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)
        turn = await session.get(Operation, turn_id, with_for_update=True)
        if (
            turn is None
            or turn.case_id != case_id
            or turn.actor_id != ctx.actor_id
            or turn.action != CHAT_TURN
        ):
            raise not_found()
        if turn.status != "PENDING":
            return {"turn_id": str(turn.id), "status": turn.status}  # ya liquidado: idempotente
        turn.status = status = "SUCCEEDED" if payload.outcome == "OK" else "FAILED_FINAL"
        turn.updated_at = now
        reservation = _reservation(turn)
        await settle(session, reservation, payload.input_tokens, payload.output_tokens)
    with svc.tracer.span(
        "agent.usage",
        metadata={
            "case_alias": f"caso-{str(case_id)[:8]}",
            "provider": payload.provider,
            "model": payload.model,
            "outcome": payload.outcome,
            "operation_id": str(turn_id),
            "input_tokens": payload.input_tokens,
            "output_tokens": payload.output_tokens,
        },
    ) as span:
        if payload.outcome == "ERROR":
            span.update(level="ERROR")
    return {"turn_id": str(turn_id), "status": status}
