"""Proyección completa del caso para la API, según el rol que la consulta."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import ActorRole, Case, HumanReview, Operation, Vehicle
from app.tools import cases
from app.tools.context import ExecutionContext
from app.tools.credit import (
    VEHICLE_IDENTITY,
    active_offers,
    active_selection,
    current_profile,
    current_quote,
    key_required,
)
from app.tools.documents import (
    active_documents,
    active_validations,
    current_extraction,
    document_view,
    in_document_stage,
    missing_slots,
)
from app.tools.ready import MATERIAL_ACTIONS
from app.tools.reviews import open_review_row, review_view


async def build_snapshot(
    session: AsyncSession, ctx: ExecutionContext, case: Case, now: datetime
) -> dict[str, Any]:
    snap = await cases.build_snapshot(session, ctx, case, now)
    profile = await current_profile(session, case, now)
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    quote = await current_quote(session, case, vehicle, now) if vehicle else None
    offers = await active_offers(session, case)
    selection = await active_selection(session, case)

    snap["credit_profile"] = None
    if profile is not None:
        snap["credit_profile"] = {
            "outcome": profile.outcome,
            "reason_code": profile.reason_code,
            "band": profile.band,
            "annual_nominal_rate": str(profile.annual_nominal_rate)
            if profile.annual_nominal_rate is not None
            else None,
            "max_financed_principal": str(profile.max_financed_principal)
            if profile.max_financed_principal is not None
            else None,
            "expires_at": profile.expires_at.isoformat(),
        }
        if ctx.role is ActorRole.ADVISOR:
            snap["credit_profile"]["score"] = profile.score
            snap["credit_profile"]["history_summary"] = profile.history_summary
    snap["key_quote"] = (
        None
        if quote is None
        else {
            "amount": str(quote.amount),
            "currency": quote.currency,
            "expires_at": quote.expires_at.isoformat(),
        }
    )
    snap["offers"] = [
        {
            "offer_id": str(o.id),
            **o.display,
            "display_hash": o.display_hash,
            "total_interest": str(o.total_payment - o.financed_principal),
            "expired": o.expires_at <= now,
        }
        for o in offers
    ]
    snap["selection"] = (
        None
        if selection is None
        else {"offer_id": str(selection.offer_id), "selected_at": selection.selected_at.isoformat()}
    )
    docs = await active_documents(session, case)
    views = []
    for doc in docs:
        view = document_view(doc, await current_extraction(session, doc))
        views.append(view)
    snap["documents"] = views
    snap["missing_documents"] = missing_slots(docs) if in_document_stage(case) else []
    snap["validations"] = [
        {"rule_id": v.rule_id, "status": v.status, "reason_code": v.reason_code,
         "refs": v.evidence_refs}
        for v in await active_validations(session, case)
    ]  # fmt: skip
    snap["correction_rounds"] = case.correction_rounds
    review = await open_review_row(session, case)
    advisor = ctx.role is ActorRole.ADVISOR
    snap["open_review"] = None if review is None else review_view(review, detailed=advisor)
    snap["advisor_request"] = case.advisor_request
    snap["ready"] = case.ready_verdict
    if advisor:
        await _advisor_detail(session, case, docs, snap)
    snap["next_action"] = _next_action(snap, case, vehicle)
    return snap


async def _advisor_detail(session: AsyncSession, case: Case, docs, snap: dict[str, Any]) -> None:
    """Evidencia para la revisión (TDD §6.8): lectura por campo, operaciones e historial."""
    by_id = {d["document_id"]: d for d in snap["documents"]}
    for doc in docs:
        extraction = await current_extraction(session, doc)
        if extraction is None:
            continue
        by_id[str(doc.id)]["extraction"] = {
            "provider": extraction.provider,
            "detected_type": extraction.extraction.get("detected_type"),
            "legible": extraction.extraction.get("legible"),
            "fields": {
                name: {
                    "value": spec.get("value"),
                    "confidence": spec.get("confidence"),
                    "human_verified": bool(spec.get("human_verified")),
                    "page": spec.get("page"),
                }
                for name, spec in extraction.extraction.get("fields", {}).items()
            },
        }
    ops = await session.scalars(
        select(Operation)
        .where(Operation.case_id == case.id, Operation.action.in_(MATERIAL_ACTIONS))
        .order_by(Operation.created_at)
    )
    snap["operations"] = [
        {
            "operation_id": str(o.id),
            "action": o.action,
            "status": o.status,
            "attempts": o.attempts,
            "provider_ref": o.provider_ref,
        }
        for o in ops
    ]
    reviews = await session.scalars(
        select(HumanReview).where(HumanReview.case_id == case.id).order_by(HumanReview.opened_at)
    )
    snap["reviews"] = [review_view(r, detailed=True) for r in reviews]


def _next_action(snap: dict[str, Any], case: Case, vehicle: Vehicle | None) -> dict[str, Any]:
    base = snap["next_action"]
    if base["type"] == "CONFIRM_DECLARATIONS":
        return base
    state = snap["workflow_state"]
    if state == "PROFILING":
        if case.wait_reason == "PROVIDER_RETRY":
            return {"type": "RETRY", "reason": "PROVIDER_UNAVAILABLE"}
        if base["type"] == "PROVIDE_PROFILE":
            return base
        missing = [f for f in VEHICLE_IDENTITY if vehicle is None or getattr(vehicle, f) is None]
        if key_required(case) and missing and snap["key_quote"] is None:
            return {"type": "PROVIDE_VEHICLE_IDENTITY", "fields": missing}
        return {"type": "WAIT_SYSTEM", "reason": "PROFILING_IN_PROGRESS"}
    if state == "SIMULATION":
        if case.wait_reason == "PROVIDER_RETRY":
            return {"type": "RETRY", "reason": "PROVIDER_UNAVAILABLE"}
        if any(not o["expired"] for o in snap["offers"]):
            return {"type": "SELECT_OFFER"}
        return {"type": "WAIT_SYSTEM", "reason": "SIMULATION_PENDING"}
    if state == "HUMAN_REVIEW":
        return {"type": "WAIT_REVIEW"}
    if state == "DOCUMENT_COLLECTION":
        if case.wait_reason == "MODEL_UNAVAILABLE":
            return {"type": "RETRY", "reason": "EXTRACTION_UNAVAILABLE"}
        return {"type": "UPLOAD_DOCUMENTS", "missing": snap["missing_documents"]}
    if state == "NEEDS_CORRECTION" and snap["advisor_request"]:
        return {"type": "CORRECT_REQUESTED", **snap["advisor_request"]}
    if state == "NEEDS_CORRECTION":
        failed = [v for v in snap["validations"] if v["status"] != "PASS"]
        return {"type": "CORRECT_DOCUMENTS",
                "reasons": [{"rule_id": v["rule_id"], "reason_code": v["reason_code"],
                             "refs": v["refs"]} for v in failed]}  # fmt: skip
    if state == "DOCUMENT_VALIDATION":
        return {"type": "WAIT_SYSTEM", "reason": "READY_GATE_PENDING"}
    return base
