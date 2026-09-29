"""Tools `propose_declarations` y `confirm_declarations` (TDD §5.2, §6.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.declarations import (
    PROFILE_FIELDS,
    VEHICLE_FIELDS,
    diff_fields,
    invalidations_for,
    missing_profile_fields,
    parse_partial,
    payload_hash,
)
from app.domain.states import WaitReason, WorkflowState, stage_index
from app.persistence.models import Case, PendingAction, Vehicle
from app.tools.cases import append_event, bump, require_mutable, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.credit import invalidate
from app.tools.documents import invalidate_validation
from app.tools.eligibility import check_vehicle_eligibility
from app.tools.errors import ActionError, not_found

# Campos cuyo valor ya fue consumido por una etapa: a partir de ella no admiten `null`.
_USED_AFTER = {
    "vehicle": (
        WorkflowState.VEHICLE_ELIGIBILITY,
        ("owned_by_customer", "blocking_debt", "has_second_key"),
    ),
    "profile": (WorkflowState.PROFILING, PROFILE_FIELDS),
}


def _validated_fields(group: str, fields: dict[str, Any]) -> dict[str, Any]:
    if group not in ("vehicle", "profile"):
        raise ActionError(422, "INVALID_DECLARATION", "Grupo de declaración desconocido.")
    try:
        parsed = parse_partial(group, fields)
    except ValidationError as exc:
        # Ubicación y motivo, nunca el valor capturado ni el contexto de la excepción.
        problems = [
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_context=False)
        ]
        raise ActionError(
            422,
            "INVALID_DECLARATION",
            "Revisa estos campos: " + "; ".join(problems),
        ) from None
    if not parsed:
        raise ActionError(422, "INVALID_DECLARATION", "La propuesta no contiene campos.")
    return parsed


def _check_null_after_use(case: Case, group: str, parsed: dict[str, Any]) -> None:
    stage, fields = _USED_AFTER[group]
    if stage_index(WorkflowState(case.workflow_state)) <= stage_index(stage):
        return
    nulled = sorted(f for f in fields if f in parsed and parsed[f] is None)
    if nulled:
        raise ActionError(
            422,
            "NULL_AFTER_USE",
            "Estos datos ya se usaron en una etapa posterior y no pueden quedar como "
            "desconocidos: " + ", ".join(nulled),
        )


async def propose_declarations(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    *,
    group: str,
    fields: dict[str, Any],
    now: datetime,
    ttl_minutes: int,
    source_message_id: uuid.UUID | None = None,
) -> PendingAction:
    require_mutable(case)
    if case.workflow_state == WorkflowState.HUMAN_REVIEW.value:
        # Pausa controlada: solo el asesor la resuelve (TDD §4.4, §6.8).
        raise ActionError(409, "CASE_IN_REVIEW", "Tu solicitud está en revisión por un asesor.")
    parsed = _validated_fields(group, fields)
    _check_null_after_use(case, group, parsed)
    await session.execute(
        update(PendingAction)
        .where(PendingAction.case_id == case.id, PendingAction.status == "PENDING")
        .values(status="SUPERSEDED")
    )
    bump(case)
    pending = PendingAction(
        id=uuid.uuid4(),
        case_id=case.id,
        kind="UPDATE_DECLARATIONS",
        group_name=group,
        typed_payload=parsed,
        payload_hash=payload_hash(group, parsed),
        source_message_id=source_message_id,
        created_by=ctx.actor_id,
        base_version=case.version,
        expires_at=now + timedelta(minutes=ttl_minutes),
        status="PENDING",
    )
    session.add(pending)
    await append_event(
        session,
        ctx,
        case,
        "DECLARATIONS_PROPOSED",
        {"group": group, "fields": sorted(parsed)},
    )
    return pending


async def _current_values(
    session: AsyncSession, case: Case, group: str
) -> tuple[dict[str, Any], Vehicle | None]:
    if group == "profile":
        return {k: case.declared_profile.get(k) for k in PROFILE_FIELDS}, None
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    if vehicle is None:
        vehicle = Vehicle(id=uuid.uuid4(), case_id=case.id)
        session.add(vehicle)
    return {k: getattr(vehicle, k) for k in VEHICLE_FIELDS}, vehicle


async def _apply_dependencies(
    session: AsyncSession, case: Case, deps: frozenset[str], now: datetime
) -> list[str]:
    applied = []
    if "eligibility" in deps and case.eligibility is not None:
        case.eligibility = None
        applied.append("eligibility")
    # El perfil de Buró no se marca: deja de estar vigente cuando cambia su fingerprint
    # (TDD §6.1). "validation" se agrega con los documentos (H4).
    applied += await invalidate(session, case, deps, now)
    if "validation" in deps and await invalidate_validation(session, case):
        applied.append("validation")
    return applied


async def confirm_declarations(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    *,
    pending_action_id: uuid.UUID,
    confirmed_hash: str,
    now: datetime,
) -> dict[str, Any]:
    require_mutable(case)
    if case.workflow_state == WorkflowState.HUMAN_REVIEW.value:
        raise ActionError(409, "CASE_IN_REVIEW", "Tu solicitud está en revisión por un asesor.")
    pending = await session.scalar(
        select(PendingAction).where(
            PendingAction.id == pending_action_id, PendingAction.case_id == case.id
        )
    )
    if pending is None:
        raise not_found()
    if pending.status != "PENDING":
        raise ActionError(409, "PENDING_ACTION_NOT_ACTIVE", "Esta propuesta ya no está vigente.")
    if pending.expires_at <= now:
        raise ActionError(409, "PENDING_ACTION_EXPIRED", "La propuesta venció; vuelve a enviarla.")
    if pending.payload_hash != confirmed_hash:
        raise ActionError(
            409, "PAYLOAD_HASH_MISMATCH", "Lo confirmado no coincide con la propuesta mostrada."
        )
    if pending.base_version != case.version:
        raise ActionError(
            409,
            "PENDING_ACTION_STALE",
            "El caso cambió después de la propuesta; genera una nueva.",
            current_version=case.version,
        )

    group = pending.group_name
    proposed = pending.typed_payload
    current, vehicle = await _current_values(session, case, group)
    changed = diff_fields(current, proposed)
    if group == "profile":
        case.declared_profile = {**case.declared_profile, **proposed}
    else:
        for name, value in proposed.items():
            setattr(vehicle, name, value)
    if changed:
        case.input_revision += 1
    pending.status = "CONFIRMED"
    bump(case)
    if changed:
        case.advisor_request = None  # datos nuevos: la solicitud del asesor quedó atendida
    event = await append_event(
        session,
        ctx,
        case,
        "DECLARATIONS_CONFIRMED",
        {"group": group, "changed_fields": sorted(changed)},
    )
    pending.confirmed_event_id = event.id
    if vehicle is not None and changed:
        vehicle.confirmed_event_id = event.id

    deps, resume = invalidations_for(changed)
    state = WorkflowState(case.workflow_state)
    moved_from = None
    if (
        resume is not None
        and state is not WorkflowState.HUMAN_REVIEW
        and stage_index(resume) < stage_index(state)
    ):
        moved_from = state
        transition(case, resume)
    applied = await _apply_dependencies(session, case, deps, now)
    if applied or moved_from is not None:
        await append_event(
            session,
            ctx,
            case,
            "DEPENDENCIES_INVALIDATED",
            {
                "dependencies": sorted(applied),
                "from_state": moved_from.value if moved_from else state.value,
                "to_state": case.workflow_state,
            },
        )

    # La API reevalúa la etapa con los datos confirmados; el modelo no decide la elegibilidad.
    if case.workflow_state == WorkflowState.VEHICLE_ELIGIBILITY.value:
        await check_vehicle_eligibility(session, ctx, case)
    elif case.workflow_state == WorkflowState.PROFILING.value:
        complete = not missing_profile_fields(case.declared_profile)
        set_wait(case, WaitReason.NONE if complete else WaitReason.CUSTOMER_INPUT)
    elif case.workflow_state == WorkflowState.SIMULATION.value and changed:
        set_wait(case, WaitReason.NONE)
    return {"group": group, "changed_fields": sorted(changed)}
