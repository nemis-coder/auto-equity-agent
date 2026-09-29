"""Tool `check_vehicle_eligibility`: aplica las reglas del challenge y la transición."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.eligibility import EligibilityResult, EligibilityStatus, evaluate_eligibility
from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import Case, Vehicle
from app.tools.cases import append_event, bump, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.errors import ActionError


async def check_vehicle_eligibility(
    session: AsyncSession, ctx: ExecutionContext, case: Case
) -> EligibilityStatus:
    if case.workflow_state != WorkflowState.VEHICLE_ELIGIBILITY.value:
        raise ActionError(409, "INVALID_STATE", "La elegibilidad solo se evalúa en su etapa.")
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    result = evaluate_eligibility(
        vehicle.owned_by_customer if vehicle else None,
        vehicle.blocking_debt if vehicle else None,
        vehicle.has_second_key if vehicle else None,
    )
    if result.status is EligibilityStatus.ELIGIBLE and (vehicle is None or not vehicle.vehicle_ref):
        # La placa no es criterio de elegibilidad, pero sin ella el documento del auto no se
        # puede cotejar: el caso no sale de esta etapa hasta tenerla (con o sin segunda llave).
        result = EligibilityResult(
            EligibilityStatus.PENDING,
            missing=("vehicle_ref",),
            key_quote_required=result.key_quote_required,
        )
    case.eligibility = {
        "status": result.status.value,
        "rejection_reasons": [r.value for r in result.rejection_reasons],
        "missing": list(result.missing),
        "key_quote_required": result.key_quote_required,
        "rules_version": result.rules_version,
        "input_revision": case.input_revision,
    }
    bump(case)
    await append_event(
        session,
        ctx,
        case,
        "ELIGIBILITY_CHECKED",
        {
            "status": result.status.value,
            "reasons": [r.value for r in result.rejection_reasons],
            "missing": list(result.missing),
            "key_quote_required": result.key_quote_required,
        },
    )
    if result.status is EligibilityStatus.REJECTED:
        transition(case, WorkflowState.REJECTED)
        set_wait(case, WaitReason.NONE)
        await append_event(
            session,
            ctx,
            case,
            "CASE_REJECTED",
            {"reasons": [r.value for r in result.rejection_reasons]},
        )
    elif result.status is EligibilityStatus.ELIGIBLE:
        transition(case, WorkflowState.PROFILING)
        set_wait(case, WaitReason.CUSTOMER_INPUT)
        await append_event(
            session,
            ctx,
            case,
            "STAGE_ENTERED",
            {
                "stage": WorkflowState.PROFILING.value,
                "key_quote_required": result.key_quote_required,
            },
        )
    else:
        set_wait(case, WaitReason.CUSTOMER_INPUT)
    return result.status
