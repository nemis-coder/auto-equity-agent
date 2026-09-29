"""Revisión humana mínima (TDD §6.8).

`open_review` es la única forma de entrar a `HUMAN_REVIEW`: crea la fila OPEN (única por caso)
y el evento. Las resoluciones son exclusivas del asesor asignado y ninguna lleva a READY:
después de resolver, el orquestador vuelve a ejecutar las reglas y el gate final.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import Case, DocumentExtraction, HumanReview, Operation
from app.tools.cases import append_event, bump, require_mutable, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.credit import active_selection
from app.tools.documents import (
    active_documents,
    current_extraction,
    invalidate_validation,
    load_document,
    validation_fingerprint,
)
from app.tools.errors import ActionError, not_found

DOCUMENT_STAGES = frozenset(
    {
        WorkflowState.DOCUMENT_COLLECTION.value,
        WorkflowState.DOCUMENT_VALIDATION.value,
        WorkflowState.NEEDS_CORRECTION.value,
    }
)
CORRECTION_TARGETS = frozenset({"IDENTITY", "INCOME", "VEHICLE_OWNERSHIP", "vehicle", "profile"})


async def open_review_row(session: AsyncSession, case: Case) -> HumanReview | None:
    return await session.scalar(
        select(HumanReview).where(HumanReview.case_id == case.id, HumanReview.status == "OPEN")
    )


async def open_review(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    *,
    reason_code: str,
    resume_state: str,
    details: dict[str, Any] | None = None,
    wait: WaitReason = WaitReason.NONE,
) -> HumanReview:
    """Pasa el caso a revisión. El llamador ya tiene el caso bloqueado e incrementó la versión."""
    existing = await open_review_row(session, case)
    if existing is not None:
        return existing
    transition(case, WorkflowState.HUMAN_REVIEW)
    set_wait(case, wait)
    review = HumanReview(
        id=uuid.uuid4(),
        case_id=case.id,
        status="OPEN",
        reason_code=reason_code,
        resume_state=resume_state,
        details=details or {},
        opened_by=ctx.actor_id,
        opened_at=datetime.now(UTC),
    )
    session.add(review)
    await session.flush()
    await append_event(
        session, ctx, case, "HUMAN_REVIEW_REQUESTED",
        {"review_id": str(review.id), "reason_code": reason_code, "resume_state": resume_state,
         **(details or {})},
    )  # fmt: skip
    return review


async def request_advisor(session: AsyncSession, ctx: ExecutionContext, case: Case) -> None:
    """El cliente pide hablar con un asesor (TDD §6.8, motivo `CUSTOMER_REQUEST`).

    Con el caso ya en revisión no hace nada: una sola revisión abierta por caso.
    """
    require_mutable(case)
    if WorkflowState(case.workflow_state) is WorkflowState.HUMAN_REVIEW:
        return
    previous = case.workflow_state
    bump(case)
    await open_review(session, ctx, case, reason_code="CUSTOMER_REQUEST", resume_state=previous)


def review_view(review: HumanReview, *, detailed: bool) -> dict[str, Any]:
    view = {
        "review_id": str(review.id),
        "status": review.status,
        "reason_code": review.reason_code,
        "opened_at": review.opened_at.isoformat() if review.opened_at else None,
    }
    if detailed:
        view |= {
            "resume_state": review.resume_state,
            "details": review.details,
            "resolution": review.resolution,
            "resolved_at": review.resolved_at.isoformat() if review.resolved_at else None,
        }
    return view


async def load_open_review(session: AsyncSession, case: Case, review_id: uuid.UUID) -> HumanReview:
    review = await session.scalar(
        select(HumanReview)
        .where(HumanReview.id == review_id, HumanReview.case_id == case.id)
        .with_for_update()
    )
    if review is None:
        raise not_found()
    if review.status != "OPEN" or case.workflow_state != WorkflowState.HUMAN_REVIEW.value:
        raise ActionError(409, "REVIEW_NOT_OPEN", "Esta revisión ya fue resuelta.")
    return review


def _close(review: HumanReview, ctx: ExecutionContext, resolution: str, payload: dict) -> None:
    review.status = "RESOLVED"
    review.resolution = resolution
    review.resolution_payload = payload
    review.resolved_by = ctx.actor_id
    review.resolved_at = datetime.now(UTC)


async def _finish(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    review: HumanReview,
    resolution: str,
    payload: dict,
    target: WorkflowState,
    wait: WaitReason = WaitReason.NONE,
) -> None:
    _close(review, ctx, resolution, payload)
    bump(case)
    transition(case, target)
    set_wait(case, wait)
    if target.value in DOCUMENT_STAGES:
        # La evidencia se vuelve a evaluar: una validación anterior nunca se reutiliza.
        await invalidate_validation(session, case)
    await append_event(
        session, ctx, case, "HUMAN_REVIEW_RESOLVED",
        {"review_id": str(review.id), "resolution": resolution, "to_state": target.value},
    )  # fmt: skip


def _resume_target(case_has_selection: bool, resume_state: str) -> WorkflowState:
    target = WorkflowState(resume_state)
    if target.value in DOCUMENT_STAGES and not case_has_selection:
        return WorkflowState.SIMULATION
    if target in (WorkflowState.NEEDS_CORRECTION,):
        return WorkflowState.DOCUMENT_COLLECTION
    return target


# --- Resoluciones ----------------------------------------------------------------------------


async def request_correction(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    review: HumanReview,
    *,
    targets: list[str],
    message: str,
) -> None:
    if not targets or not set(targets) <= CORRECTION_TARGETS:
        raise ActionError(422, "INVALID_TARGETS", "Indica qué datos o documentos corregir.")
    if {"IDENTITY", "INCOME", "VEHICLE_OWNERSHIP"} & set(targets) and not await active_selection(
        session, case
    ):
        raise ActionError(
            409, "RESOLUTION_NOT_APPLICABLE", "El caso aún no está en la etapa de documentos."
        )
    case.advisor_request = {"targets": sorted(set(targets)), "message": message,
                            "review_id": str(review.id)}  # fmt: skip
    await _finish(
        session, ctx, case, review, "REQUEST_CORRECTION",
        {"targets": sorted(set(targets)), "message": message},
        WorkflowState.NEEDS_CORRECTION, WaitReason.CUSTOMER_INPUT,
    )  # fmt: skip


async def amend_extraction(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    review: HumanReview,
    *,
    document_id: uuid.UUID,
    fields: dict[str, Any],
    reason: str,
) -> None:
    """Lectura humana por campo: `human_verified=true`, nunca una confianza ficticia de 1,0."""
    doc = await load_document(session, case, document_id)
    if not doc.active:
        raise ActionError(409, "DOCUMENT_NOT_CURRENT", "El documento ya no es el vigente.")
    if not await active_selection(session, case):
        raise ActionError(
            409, "RESOLUTION_NOT_APPLICABLE", "El caso aún no está en la etapa de documentos."
        )
    previous = await current_extraction(session, doc)
    if previous is None:
        raise ActionError(409, "EXTRACTION_MISSING", "El documento aún no tiene lectura.")
    known = previous.extraction.get("fields", {})
    if not fields or not set(fields) <= set(known):
        raise ActionError(422, "INVALID_FIELDS", "Solo se corrigen campos del documento.")
    amended = copy.deepcopy(previous.extraction)
    audit = []
    for name, value in fields.items():
        if value is not None and not isinstance(value, str | int | float):
            raise ActionError(422, "INVALID_FIELDS", "Valor de campo no admitido.")
        old = known[name]
        amended["fields"][name] = {
            **old,
            "value": None if value is None else str(value),
            "confidence": None,
            "human_verified": True,
        }
        audit.append(
            {"field": name, "old": old.get("value"), "new": amended["fields"][name]["value"]}
        )
    previous.status = "SUPERSEDED"
    await session.flush()
    new = DocumentExtraction(
        id=uuid.uuid4(),
        case_id=case.id,
        document_id=doc.id,
        document_sha256=doc.sha256,
        schema_version=previous.schema_version,
        provider="HUMAN",
        model="advisor",
        prompt_version="human-v1",
        extraction=amended,
        status="CURRENT",
        supersedes_id=previous.id,
        human_override_actor_id=ctx.actor_id,
    )
    session.add(new)
    bump(case)
    await append_event(
        session, ctx, case, "EXTRACTION_AMENDED",
        {"document_id": str(doc.id), "extraction_id": str(new.id), "fields": sorted(fields)},
    )  # fmt: skip
    # Valores anterior y nuevo: solo en la fila de la revisión (lectura del asesor), no en
    # eventos ni trazas.
    await _finish(
        session, ctx, case, review, "AMEND_EXTRACTION",
        {"document_id": str(doc.id), "extraction_id": str(new.id), "changes": audit,
         "reason": reason},
        WorkflowState.DOCUMENT_VALIDATION,
    )  # fmt: skip


async def unknown_operations(session: AsyncSession, case: Case) -> list[Operation]:
    rows = await session.scalars(
        select(Operation).where(Operation.case_id == case.id, Operation.status == "UNKNOWN")
    )
    return list(rows)


async def apply_reconciliation(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    review: HumanReview,
    *,
    operation: Operation,
    outcome: str,
    provider_ref: str | None,
    found: dict[str, Any] | None,
) -> bool:
    """Aplica una conciliación verificada contra el proveedor. Devuelve si cerró la revisión."""
    if outcome == "EFFECT_CONFIRMED":
        if found is None or str(found.get("provider_ref")) != (provider_ref or ""):
            raise ActionError(
                409, "RECONCILIATION_MISMATCH",
                "El proveedor no confirma ese efecto con esa referencia.",
            )  # fmt: skip
        # Se guarda como resultado de la operación: el siguiente paso lo aplica sin llamar
        # otra vez al proveedor (un solo efecto lógico, D18).
        operation.status, operation.result = "SUCCEEDED", found
        operation.provider_ref = str(found.get("provider_ref"))[:128]
    else:
        if found is not None:
            raise ActionError(
                409,
                "RECONCILIATION_MISMATCH",
                "El proveedor sí registró un efecto para esta clave.",
            )
        operation.status = "FAILED_RETRYABLE"  # sin efecto comprobado: reintento seguro
    operation.lease_expires_at = None
    operation.updated_at = datetime.now(UTC)
    bump(case)
    await append_event(
        session, ctx, case, "OPERATION_RECONCILED",
        {"operation_id": str(operation.id), "action": operation.action, "outcome": outcome},
        operation_id=operation.id,
    )  # fmt: skip
    await session.flush()
    if await unknown_operations(session, case):
        return False
    await _finish(
        session, ctx, case, review, "RECONCILE_OPERATION",
        {"operation_id": str(operation.id), "outcome": outcome, "provider_ref": provider_ref},
        _resume_target(bool(await active_selection(session, case)), review.resume_state),
    )  # fmt: skip
    return True


async def _reason_resolved(session: AsyncSession, case: Case, review: HumanReview) -> bool:
    if review.reason_code == "PROVIDER_UNKNOWN":
        return not await unknown_operations(session, case)
    if review.reason_code == "DOCUMENT_CORRECTIONS_EXHAUSTED":
        pairs = []
        for doc in await active_documents(session, case):
            extraction = await current_extraction(session, doc)
            if extraction is not None:
                pairs.append((doc, extraction))
        # Solo evidencia nueva (otro archivo o una lectura humana) justifica reanudar.
        return validation_fingerprint(case, pairs) != case.last_failed_validation_fp
    if review.reason_code in (
        "CUSTOMER_REQUEST",
        "TURN_BUDGET_EXCEEDED",
        "TOKEN_BUDGET_EXCEEDED",
        "LEGACY",
    ):
        return True
    # Perfil del Buró en revisión u otros motivos: sin datos nuevos no hay nada que reanudar.
    return False


async def resume(
    session: AsyncSession, ctx: ExecutionContext, case: Case, review: HumanReview
) -> None:
    if not await _reason_resolved(session, case, review):
        raise ActionError(
            409, "REVIEW_REASON_UNRESOLVED",
            "El motivo de la revisión sigue vigente; pide una corrección o registra la lectura.",
        )  # fmt: skip
    target = _resume_target(bool(await active_selection(session, case)), review.resume_state)
    payload: dict[str, Any] = {}
    if review.reason_code == "TOKEN_BUDGET_EXCEEDED":
        # Revisión del presupuesto (§5.4): el asesor concede una asignación adicional.
        case.token_allotments += 1
        payload = {"token_allotments": case.token_allotments}
    await _finish(session, ctx, case, review, "RESUME", payload, target)
