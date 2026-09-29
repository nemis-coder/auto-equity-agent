"""Orquestación determinística del expediente (decisiones 0017, 0037 y 0038).

Elige la acción necesaria según el estado durable y ejecuta la tool correspondiente con sus
propias guardas. La API conserva estas decisiones de negocio; el grafo de la conversación
solicita acciones por HTTP y usa sus resultados para guiar al cliente. Este orquestador no
llama al modelo, no selecciona ofertas ni confirma datos por el cliente.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.declarations import missing_profile_fields
from app.domain.documents import all_pass
from app.domain.readiness import IN_REVIEW, NOT_APPLICABLE, WAIT_CUSTOMER, WAIT_PROVIDER
from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import Case, Vehicle
from app.tools.cases import append_event, bump, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.credit import (
    VEHICLE_IDENTITY,
    Runtime,
    active_offers,
    current_profile,
    current_quote,
    invalidate,
    key_required,
    query_credit_bureau,
    quote_second_key,
    request_offers,
    service_context,
)
from app.tools.documents import (
    active_documents,
    active_validations,
    extract_document,
    missing_slots,
    pending_extractions,
    run_validation,
)
from app.tools.eligibility import check_vehicle_eligibility
from app.tools.ready import mark_ready_for_financial
from app.tools.reviews import open_review

MAX_STEPS = 16
GATE_WAITS = frozenset({NOT_APPLICABLE, IN_REVIEW, WAIT_CUSTOMER, WAIT_PROVIDER})


async def _local_step(rt: Runtime, origin: ExecutionContext, case_id: uuid.UUID) -> str | None:
    """Ejecuta un paso local (sin red) bajo bloqueo. Devuelve la acción remota pendiente."""
    async with rt.sessionmaker() as session, session.begin():
        case = await session.scalar(select(Case).where(Case.id == case_id).with_for_update())
        state = WorkflowState(case.workflow_state)
        now = rt.clock.business_now()
        svc_ctx = await service_context(session, origin)

        if state is WorkflowState.VEHICLE_ELIGIBILITY:
            if case.eligibility is None:
                await check_vehicle_eligibility(session, svc_ctx, case)
                return "CONTINUE"
            return None

        if state is WorkflowState.PROFILING:
            if missing_profile_fields(case.declared_profile):
                set_wait(case, WaitReason.CUSTOMER_INPUT)
                return None
            profile = await current_profile(session, case, now)
            if profile is None:
                return "BUREAU"
            if profile.outcome == "REVIEW":
                bump(case)
                await open_review(
                    session, svc_ctx, case,
                    reason_code=profile.reason_code, resume_state=WorkflowState.PROFILING.value,
                )  # fmt: skip
                return None
            if key_required(case):
                vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
                if await current_quote(session, case, vehicle, now) is None:
                    if any(getattr(vehicle, f) is None for f in VEHICLE_IDENTITY):
                        set_wait(case, WaitReason.CUSTOMER_INPUT)
                        return None
                    return "KEY_QUOTE"
            bump(case)
            transition(case, WorkflowState.SIMULATION)
            await append_event(
                session, svc_ctx, case, "STAGE_ENTERED", {"stage": WorkflowState.SIMULATION.value}
            )
            return "CONTINUE"

        if state is WorkflowState.SIMULATION:
            profile = await current_profile(session, case, now)
            vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
            quote = await current_quote(session, case, vehicle, now) if key_required(case) else None
            if profile is None or profile.outcome != "OK" or (key_required(case) and not quote):
                # Perfil o cotización vencidos: se renuevan antes de volver a simular.
                bump(case)
                transition(case, WorkflowState.PROFILING)
                await append_event(
                    session,
                    svc_ctx,
                    case,
                    "DEPENDENCIES_INVALIDATED",
                    {
                        "dependencies": ["offers"],
                        "from_state": "SIMULATION",
                        "to_state": "PROFILING",
                        "reason": "INPUT_EXPIRED",
                    },
                )
                await invalidate(session, case, frozenset({"offers"}), now)
                return "CONTINUE"
            offers = await active_offers(session, case)
            if offers and all(o.expires_at <= now for o in offers):
                await invalidate(session, case, frozenset({"offers"}), now)
                offers = []
            if offers:
                return None
            if case.simulation_result is not None:
                return None  # el cotizador no dio opciones: el caso ya está con un asesor
            return "OFFERS"

        if state is WorkflowState.DOCUMENT_COLLECTION:
            pending = await pending_extractions(session, case)
            if pending:
                return f"EXTRACT:{pending[0].id}"
            if missing_slots(await active_documents(session, case)):
                set_wait(case, WaitReason.DOCUMENT_UPLOAD)
                return None
            transition(case, WorkflowState.DOCUMENT_VALIDATION)
            results = await run_validation(session, svc_ctx, case, now)
            return "CONTINUE" if all_pass(results) else None

        if state is WorkflowState.DOCUMENT_VALIDATION:
            if not await active_validations(session, case):
                # Tras una reanudación o una lectura humana: se revalida antes del gate.
                results = await run_validation(session, svc_ctx, case, now)
                return "CONTINUE" if all_pass(results) else None
            verdict = await mark_ready_for_financial(session, svc_ctx, case, now)
            if verdict.all_pass:
                return None
            # Recuperación aplicada: se sigue avanzando salvo que haya que esperar a alguien.
            return "CONTINUE" if verdict.recovery not in GATE_WAITS else None
        return None


async def advance(
    rt: Runtime, origin: ExecutionContext, case_id: uuid.UUID, *, retry_failed: bool = False
) -> list[str]:
    """Avanza el caso hasta necesitar al cliente, un humano o un proveedor en espera."""
    trail: list[str] = []
    for _ in range(MAX_STEPS):
        step = await _local_step(rt, origin, case_id)
        if step is None:
            break
        trail.append(step)
        if step == "CONTINUE":
            continue
        if step.startswith("EXTRACT:"):
            outcome = await extract_document(
                rt.sessionmaker, rt.store, rt.extractor, origin, case_id,
                uuid.UUID(step.removeprefix("EXTRACT:")), token_budget=rt.token_budget,
                tracer=rt.tracer,
            )  # fmt: skip
            trail.append(outcome)
            if outcome not in ("OK", "ALREADY_EXTRACTED", "DISCARDED"):
                break
            continue
        call = {"BUREAU": query_credit_bureau, "KEY_QUOTE": quote_second_key,
                "OFFERS": request_offers}[step]  # fmt: skip
        outcome = await call(rt, origin, case_id, retry_failed=retry_failed)
        trail.append(outcome)
        if outcome != "OK":
            break
        retry_failed = False
    return trail
