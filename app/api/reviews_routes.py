"""Bandeja y resoluciones de revisión humana (TDD §6.8). Solo el asesor asignado."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.cases_routes import _advance_and_snapshot, _response
from app.api.deps import execution_context, idempotency_key, if_match, services
from app.persistence.models import ActorRole, Case, HumanReview, Operation
from app.providers.base import ProviderError
from app.tools import reviews as R
from app.tools.cases import idempotent, load_case, require_role, require_version
from app.tools.context import ExecutionContext
from app.tools.errors import ActionError, not_found
from app.tools.snapshot import build_snapshot

router = APIRouter()
PROVIDER_ACTIONS = ("query_credit_bureau", "quote_second_key", "quote_offers")


def _provider_for(svc, action: str):
    """Proveedor de una operación conciliable (Buró, llave o cotizador de ofertas)."""
    return {"query_credit_bureau": svc.bureau, "quote_second_key": svc.key_provider,
            "quote_offers": svc.offer_provider}[action]  # fmt: skip


Ctx = Annotated[ExecutionContext, Depends(execution_context)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestCorrectionIn(_In):
    resolution: Literal["REQUEST_CORRECTION"]
    targets: list[str] = Field(min_length=1, max_length=5)
    message: str = Field(min_length=1, max_length=500)


class AmendExtractionIn(_In):
    resolution: Literal["AMEND_EXTRACTION"]
    document_id: uuid.UUID
    fields: dict[str, str | int | float | None] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=3, max_length=300)


class ReconcileIn(_In):
    resolution: Literal["RECONCILE_OPERATION"]
    operation_id: uuid.UUID
    outcome: Literal["EFFECT_CONFIRMED", "NO_EFFECT"]
    provider_ref: str | None = Field(default=None, max_length=128)


class ResumeIn(_In):
    resolution: Literal["RESUME"]


ResolutionIn = Annotated[
    RequestCorrectionIn | AmendExtractionIn | ReconcileIn | ResumeIn,
    Field(discriminator="resolution"),
]


@router.get("/reviews")
async def inbox(request: Request, ctx: Ctx, status: Literal["OPEN", "RESOLVED"] = "OPEN"):
    require_role(ctx, ActorRole.ADVISOR)
    async with services(request).sessionmaker() as session:
        rows = await session.execute(
            select(HumanReview, Case.workflow_state)
            .join(Case, Case.id == HumanReview.case_id)
            .where(HumanReview.status == status, Case.assigned_advisor_id == ctx.actor_id)
            .order_by(HumanReview.opened_at)
            .limit(100)
        )
        items = [
            {"case_id": str(r.case_id), **R.review_view(r, detailed=True), "case_state": state}
            for r, state in rows
        ]
    return {"items": items}


@router.post("/cases/{case_id}/reviews/{review_id}/resolutions")
async def resolve(
    case_id: uuid.UUID, review_id: uuid.UUID, payload: ResolutionIn, request: Request, ctx: Ctx
) -> JSONResponse:
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    now = svc.clock.business_now()

    found: dict[str, Any] | None = None
    if isinstance(payload, ReconcileIn):
        # La consulta al proveedor ocurre fuera del bloqueo del caso (TDD §6.9).
        async with svc.sessionmaker() as session:
            case = await load_case(session, ctx, case_id, for_update=False)
            require_role(ctx, ActorRole.ADVISOR)
            op = await session.get(Operation, payload.operation_id)
            if op is None or op.case_id != case.id:
                raise not_found()
            if op.status != "UNKNOWN":
                raise ActionError(409, "OPERATION_NOT_UNKNOWN", "La operación no está incierta.")
            op_key, action = op.idempotency_key, op.action
        provider = _provider_for(svc, action)
        try:
            found = await provider.lookup(op_key)
        except (ProviderError, TimeoutError) as exc:
            raise ActionError(
                503, "PROVIDER_UNAVAILABLE", "No se pudo consultar al proveedor.", retryable=True
            ) from exc

    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.ADVISOR)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            review = await R.load_open_review(session, case, review_id)
            if isinstance(payload, RequestCorrectionIn):
                await R.request_correction(
                    session, ctx, case, review, targets=payload.targets, message=payload.message
                )
            elif isinstance(payload, AmendExtractionIn):
                await R.amend_extraction(
                    session, ctx, case, review, document_id=payload.document_id,
                    fields=payload.fields, reason=payload.reason,
                )  # fmt: skip
            elif isinstance(payload, ReconcileIn):
                operation = await session.get(Operation, payload.operation_id, with_for_update=True)
                if operation is None or operation.case_id != case.id:
                    raise not_found()
                if operation.status != "UNKNOWN":
                    raise ActionError(
                        409, "OPERATION_NOT_UNKNOWN", "La operación no está incierta."
                    )
                await R.apply_reconciliation(
                    session, ctx, case, review, operation=operation, outcome=payload.outcome,
                    provider_ref=payload.provider_ref, found=found,
                )  # fmt: skip
            else:
                await R.resume(session, ctx, case, review)
            return 200, await build_snapshot(session, ctx, case, now)

        status, body, replay = await idempotent(
            session, ctx, action=f"resolve_review:{payload.resolution}", key=key,
            payload={"review_id": str(review_id), **payload.model_dump(mode="json")},
            case_id=case.id, execute=execute,
        )  # fmt: skip
    if replay or body.get("workflow_state") == "HUMAN_REVIEW":
        return _response(status, body, replay)
    # Tras resolver, las reglas y el gate se vuelven a ejecutar desde el estado durable.
    return await _advance_and_snapshot(request, ctx, case_id, retry_failed=True)


@router.post("/cases/{case_id}/review-requests")
async def request_review(case_id: uuid.UUID, request: Request, ctx: Ctx) -> JSONResponse:
    """El cliente pide un asesor. Idempotente; con el caso ya en revisión no abre otra."""
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    now = svc.clock.business_now()
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            await R.request_advisor(session, ctx, case)
            return 200, await build_snapshot(session, ctx, case, now)

        status, body, replay = await idempotent(
            session, ctx, action="request_review", key=key, payload={},
            case_id=case.id, execute=execute,
        )  # fmt: skip
    return _response(status, body, replay)


@router.get("/cases/{case_id}/operations/{operation_id}/provider-status")
async def provider_status(
    case_id: uuid.UUID, operation_id: uuid.UUID, request: Request, ctx: Ctx
) -> dict[str, Any]:
    """Consulta verificable al proveedor por la clave idempotente (sin repetir el efecto)."""
    svc = services(request)
    async with svc.sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        require_role(ctx, ActorRole.ADVISOR)
        op = await session.get(Operation, operation_id)
        if op is None or op.case_id != case.id:
            raise not_found()
        op_key, action, status = op.idempotency_key, op.action, op.status
    if action not in PROVIDER_ACTIONS:
        raise not_found()
    provider = _provider_for(svc, action)
    try:
        found = await provider.lookup(op_key)
    except (ProviderError, TimeoutError) as exc:
        raise ActionError(
            503, "PROVIDER_UNAVAILABLE", "No se pudo consultar al proveedor.", retryable=True
        ) from exc
    return {
        "operation_id": str(operation_id),
        "action": action,
        "status": status,
        "effect_found": found is not None,
        "provider_ref": None if found is None else str(found.get("provider_ref")),
    }
