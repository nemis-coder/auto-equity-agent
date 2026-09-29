"""Acceso autorizado a casos, eventos, idempotencia y proyección del estado."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.declarations import (
    PROFILE_FIELDS,
    VEHICLE_ELIGIBILITY_FIELDS,
    VEHICLE_FIELDS,
    missing_profile_fields,
)
from app.domain.states import MAIN_PATH, TERMINAL_STATES, WaitReason, WorkflowState
from app.domain.transitions import check_transition
from app.persistence.models import (
    Actor,
    ActorRole,
    Case,
    CaseEvent,
    Operation,
    PendingAction,
    Vehicle,
)
from app.tools.context import ExecutionContext
from app.tools.errors import ActionError, forbidden, not_found

# Una llamada del agente de la conversación al modelo (presupuesto por caso, TDD §5.4).
CHAT_TURN = "CHAT_TURN"


def _visible_filter(ctx: ExecutionContext):
    if ctx.role is ActorRole.CUSTOMER:
        return Case.customer_id == ctx.customer_id
    if ctx.role is ActorRole.ADVISOR:
        return Case.assigned_advisor_id == ctx.actor_id
    # SERVICE no ve casos por HTTP. El grafo usa el token del cliente; las operaciones
    # internas de la API construyen su contexto de servicio después de autorizar al actor.
    return Case.id.is_(None)


async def load_case(
    session: AsyncSession, ctx: ExecutionContext, case_id: uuid.UUID, *, for_update: bool
) -> Case:
    stmt = select(Case).where(Case.id == case_id, _visible_filter(ctx))
    if for_update:
        stmt = stmt.with_for_update()
    case = await session.scalar(stmt)
    if case is None:
        raise not_found()
    return case


async def list_cases(
    session: AsyncSession, ctx: ExecutionContext, *, limit: int, offset: int
) -> list[Case]:
    rows = await session.scalars(
        select(Case)
        .where(_visible_filter(ctx))
        .order_by(Case.updated_at.desc(), Case.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


def require_role(ctx: ExecutionContext, *roles: ActorRole) -> None:
    if ctx.role not in roles:
        raise forbidden()


def require_mutable(case: Case) -> None:
    if WorkflowState(case.workflow_state) in TERMINAL_STATES:
        raise ActionError(409, "TERMINAL_CASE", "El caso ya está cerrado; solo admite lectura.")


def require_version(case: Case, expected: int) -> None:
    if case.version != expected:
        raise ActionError(
            409,
            "VERSION_CONFLICT",
            "El caso cambió; recarga y vuelve a intentarlo.",
            current_version=case.version,
        )


async def append_event(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    operation_id: uuid.UUID | None = None,
) -> CaseEvent:
    event = CaseEvent(
        id=uuid.uuid4(),
        case_id=case.id,
        case_version=case.version,
        event_type=event_type,
        actor_id=ctx.actor_id,
        delegated_for=ctx.delegated_for,
        operation_id=operation_id,
        correlation_id=ctx.correlation_id,
        safe_payload=payload or {},
    )
    session.add(event)
    return event


def bump(case: Case) -> None:
    case.version += 1
    case.updated_at = datetime.now(UTC)


def transition(case: Case, target: WorkflowState) -> None:
    check_transition(WorkflowState(case.workflow_state), target)
    case.workflow_state = target.value


def input_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def idempotent(
    session: AsyncSession,
    ctx: ExecutionContext,
    *,
    action: str,
    key: str,
    payload: Any,
    case_id: uuid.UUID | None,
    execute: Callable[[uuid.UUID], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any], bool]:
    """Ejecuta un comando una sola vez por (actor, clave). Devuelve (status, body, replay).

    Debe llamarse dentro de la transacción y, si hay caso, después de bloquearlo, para que
    dos requests con la misma clave queden serializados.
    """
    digest = input_hash({"action": action, "case_id": str(case_id), "payload": payload})
    prior = await session.scalar(
        select(Operation).where(
            Operation.actor_id == ctx.actor_id, Operation.idempotency_key == key
        )
    )
    if prior is not None:
        if prior.input_hash != digest:
            raise ActionError(409, "IDEMPOTENCY_CONFLICT", "La clave ya se usó con otro contenido.")
        return prior.http_status or 200, prior.result or {}, True
    op_id = uuid.uuid4()
    status, body = await execute(op_id)
    session.add(
        Operation(
            id=op_id,
            actor_id=ctx.actor_id,
            case_id=case_id,
            action=action,
            idempotency_key=key,
            input_hash=digest,
            status="SUCCEEDED",
            http_status=status,
            result=body,
        )
    )
    return status, body, False


async def empty_draft(session: AsyncSession, customer_id: uuid.UUID) -> Case | None:
    """Solicitud del cliente que aún no empezó: sin datos confirmados, propuestas ni turnos del
    agente (una conversación ya abierta sobre el caso).

    «Nueva solicitud» la reutiliza en lugar de acumular casos vacíos. El llamador debe tener
    bloqueada la fila del cliente para que dos clics con claves distintas no creen dos casos.
    """
    return await session.scalar(
        select(Case)
        .where(
            Case.customer_id == customer_id,
            Case.workflow_state == WorkflowState.VEHICLE_ELIGIBILITY.value,
            Case.input_revision == 0,
            ~select(PendingAction.id).where(PendingAction.case_id == Case.id).exists(),
            ~select(Operation.id)
            .where(Operation.case_id == Case.id, Operation.action == CHAT_TURN)
            .exists(),
        )
        .order_by(Case.created_at.desc())
        .limit(1)
    )


async def pick_advisor(session: AsyncSession) -> uuid.UUID | None:
    return await session.scalar(
        select(Actor.id)
        .where(Actor.role == ActorRole.ADVISOR, Actor.active.is_(True))
        .order_by(Actor.alias)
        .limit(1)
    )


# --- Proyección -------------------------------------------------------------------------


def _vehicle_dict(vehicle: Vehicle | None) -> dict[str, Any]:
    return {name: getattr(vehicle, name) if vehicle else None for name in VEHICLE_FIELDS}


def _progress(state: WorkflowState) -> list[dict[str, str]]:
    if state is WorkflowState.REJECTED:
        return [
            {"stage": s.value, "status": "rejected" if i == 0 else "not_reached"}
            for i, s in enumerate(MAIN_PATH)
        ]
    if state is WorkflowState.READY_FOR_FINANCIAL:
        return [{"stage": s.value, "status": "done"} for s in MAIN_PATH]
    if state is WorkflowState.NEEDS_CORRECTION:
        current = MAIN_PATH.index(WorkflowState.DOCUMENT_COLLECTION)
    else:
        current = MAIN_PATH.index(state) if state in MAIN_PATH else len(MAIN_PATH) - 1
    return [
        {
            "stage": s.value,
            "status": "done" if i < current else "current" if i == current else "pending",
        }
        for i, s in enumerate(MAIN_PATH)
    ]


def _next_action(case: Case, vehicle: dict, profile: dict, pending: PendingAction | None) -> dict:
    state = WorkflowState(case.workflow_state)
    if pending is not None:
        return {"type": "CONFIRM_DECLARATIONS", "pending_action_id": str(pending.id)}
    if state in TERMINAL_STATES:
        return {"type": "NONE"}
    if state is WorkflowState.VEHICLE_ELIGIBILITY:
        missing = [f for f in VEHICLE_ELIGIBILITY_FIELDS if vehicle[f] is None]
        if not missing and vehicle["vehicle_ref"] is None:
            missing = ["vehicle_ref"]  # el auto califica, pero falta la placa para cotejarla
        return {"type": "PROVIDE_VEHICLE_ANSWERS", "fields": missing}
    if state is WorkflowState.PROFILING:
        missing = missing_profile_fields(profile)
        if missing:
            return {"type": "PROVIDE_PROFILE", "fields": missing}
        return {"type": "WAIT_SYSTEM", "reason": "BUREAU_PENDING"}
    return {"type": "WAIT_SYSTEM", "reason": "NEXT_STAGE_PENDING"}


async def build_snapshot(
    session: AsyncSession, ctx: ExecutionContext, case: Case, now: datetime
) -> dict[str, Any]:
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    pending = await session.scalar(
        select(PendingAction).where(
            PendingAction.case_id == case.id,
            PendingAction.status == "PENDING",
            PendingAction.expires_at > now,
        )
    )
    vehicle_data = _vehicle_dict(vehicle)
    profile = {name: case.declared_profile.get(name) for name in PROFILE_FIELDS}
    state = WorkflowState(case.workflow_state)
    return {
        "case_id": str(case.id),
        "case_version": case.version,
        "input_revision": case.input_revision,
        "workflow_state": state.value,
        "wait_reason": case.wait_reason,
        "policy_version": case.policy_version,
        "progress": _progress(state),
        "vehicle": vehicle_data,
        "profile": profile,
        "eligibility": case.eligibility,
        "missing_fields": {
            # `declared_owner_name` es opcional: la UI ya no lo pide (el titular del documento
            # se coteja con el nombre del perfil), así que no cuenta como faltante.
            "vehicle": [
                f for f in VEHICLE_FIELDS if vehicle_data[f] is None and f != "declared_owner_name"
            ],
            "profile": missing_profile_fields(profile),
        },
        "pending_action": None
        if pending is None
        else {
            "pending_action_id": str(pending.id),
            "kind": pending.kind,
            "group": pending.group_name,
            "fields": pending.typed_payload,
            "payload_hash": pending.payload_hash,
            "expires_at": pending.expires_at.isoformat(),
        },
        "next_action": _next_action(case, vehicle_data, profile, pending),
        "viewer_role": ctx.role.value,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def set_wait(case: Case, reason: WaitReason) -> None:
    case.wait_reason = reason.value
