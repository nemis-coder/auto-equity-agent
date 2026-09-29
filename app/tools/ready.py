"""Tool `mark_ready_for_financial` (TDD §6.7).

Se ejecuta con el caso bloqueado (`SELECT … FOR UPDATE`), recarga toda la evidencia y
recalcula las reglas con la función pura `readiness`. No llama a proveedores ni al modelo.
Si algo falla, aplica la recuperación determinística; nunca deja un READY a medias.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import readiness as R
from app.domain.declarations import bureau_fingerprint, missing_profile_fields, monthly_income
from app.domain.documents import ActiveDocument, evaluate
from app.domain.policy import load_policy
from app.domain.simulation import offer_hash
from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import (
    Case,
    CreditProfile,
    HumanReview,
    KeyQuote,
    Operation,
    PendingAction,
    Selection,
    Simulation,
    Vehicle,
)
from app.tools.cases import append_event, bump, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.credit import active_selection, invalidate, vehicle_fingerprint
from app.tools.documents import (
    active_documents,
    active_validations,
    current_extraction,
    invalidate_validation,
    validation_fingerprint,
)

MATERIAL_ACTIONS = ("query_credit_bureau", "quote_second_key", "quote_offers")


def _extraction_hash(extraction: dict) -> str:
    return hashlib.sha256(json.dumps(extraction, sort_keys=True).encode()).hexdigest()


async def gather_evidence(session: AsyncSession, case: Case, now: datetime) -> R.Evidence:
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    profile_data = case.declared_profile
    complete = not missing_profile_fields(profile_data)
    pending = await session.scalar(
        select(func.count())
        .select_from(PendingAction)
        .where(
            PendingAction.case_id == case.id,
            PendingAction.status == "PENDING",
            PendingAction.expires_at > now,
        )
    )
    selection = await active_selection(session, case)
    offer = await session.get(Simulation, selection.offer_id) if selection else None
    profile = await session.get(CreditProfile, offer.profile_id) if offer else None
    quote = await session.get(KeyQuote, offer.quote_id) if offer and offer.quote_id else None
    if quote is None and vehicle is not None and vehicle.has_second_key is False:
        quote = await session.scalar(
            select(KeyQuote)
            .where(KeyQuote.case_id == case.id, KeyQuote.status == "VALID")
            .order_by(KeyQuote.issued_at.desc())
            .limit(1)
        )
    last_revoked = await session.scalar(
        select(func.max(Selection.revoked_at)).where(Selection.case_id == case.id)
    )

    docs, pairs, by_slot = [], [], {}
    for doc in await active_documents(session, case):
        extraction = await current_extraction(session, doc)
        docs.append(
            R.DocumentEvidence(
                id=str(doc.id),
                slot=doc.slot,
                kind=doc.kind,
                sha256=doc.sha256,
                extraction_id=str(extraction.id) if extraction else None,
                extraction_sha256=extraction.document_sha256 if extraction else None,
                extraction_hash=_extraction_hash(extraction.extraction) if extraction else None,
            )
        )
        if extraction is not None:
            pairs.append((doc, extraction))
        by_slot[doc.slot] = ActiveDocument(
            str(doc.id), doc.kind, extraction.extraction if extraction else None
        )
    rules = (
        evaluate(by_slot, profile_data, {"vehicle_ref": vehicle.vehicle_ref}, now)
        if complete and vehicle is not None
        else []
    )
    stored = {v.input_fingerprint for v in await active_validations(session, case)}

    open_review = await session.scalar(
        select(func.count())
        .select_from(HumanReview)
        .where(HumanReview.case_id == case.id, HumanReview.status == "OPEN")
    )
    open_ops = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.case_id == case.id,
            Operation.action.in_(MATERIAL_ACTIONS),
            Operation.status.in_(("PENDING", "UNKNOWN")),
        )
    )
    policy = load_policy(case.policy_version)
    capacity = (
        monthly_income(profile_data["income"]) * policy.max_payment_to_income_ratio
        if complete
        else None
    )
    return R.Evidence(
        state=case.workflow_state,
        input_revision=case.input_revision,
        policy_version=case.policy_version,
        profile_complete=complete,
        pending_proposal=bool(pending),
        owned_by_customer=vehicle.owned_by_customer if vehicle else None,
        blocking_debt=vehicle.blocking_debt if vehicle else None,
        has_second_key=vehicle.has_second_key if vehicle else None,
        eligibility=case.eligibility,
        bureau_fingerprint=bureau_fingerprint(profile_data) if complete else None,
        vehicle_fingerprint=vehicle_fingerprint(vehicle) if vehicle else None,
        payment_capacity=capacity,
        profile=None
        if profile is None
        else R.ProfileEvidence(
            id=str(profile.id),
            outcome=profile.outcome,
            expires_at=profile.expires_at,
            policy_version=profile.policy_version,
            request_fingerprint=profile.request_fingerprint,
            annual_nominal_rate=profile.annual_nominal_rate,
            max_financed_principal=profile.max_financed_principal,
        ),
        quote=None
        if quote is None
        else R.QuoteEvidence(
            id=str(quote.id),
            amount=quote.amount,
            expires_at=quote.expires_at,
            status=quote.status,
            request_fingerprint=quote.request_fingerprint,
        ),
        offer=None
        if offer is None
        else R.OfferEvidence(
            id=str(offer.id),
            status=offer.status,
            expires_at=offer.expires_at,
            profile_id=str(offer.profile_id),
            quote_id=str(offer.quote_id) if offer.quote_id else None,
            cash_amount=offer.cash_amount,
            key_cost=offer.key_cost,
            financed_principal=offer.financed_principal,
            annual_nominal_rate=offer.annual_nominal_rate,
            term_months=offer.term_months,
            regular_payment=offer.regular_payment,
            last_payment=offer.last_payment,
            total_payment=offer.total_payment,
            schedule=tuple(offer.schedule),
            display_hash=offer.display_hash,
            computed_display_hash=offer_hash(offer.display),
            policy_version=offer.policy_version,
        ),
        selection=None
        if selection is None
        else R.SelectionEvidence(
            offer_id=str(selection.offer_id),
            displayed_offer_hash=selection.displayed_offer_hash,
            selected_at=selection.selected_at,
        ),
        last_invalidation_at=last_revoked,
        documents=tuple(docs),
        rules_now=tuple((r.rule_id, r.status.value) for r in rules),
        validation_fingerprint=next(iter(stored)) if len(stored) == 1 else None,
        current_validation_fingerprint=validation_fingerprint(case, pairs),
        open_review=bool(open_review),
        open_material_operations=open_ops,
        advisor_request_open=case.advisor_request is not None,
    )


# Dependencias que se invalidan al volver a cada etapa.
RECOVERY_INVALIDATIONS = {
    WorkflowState.VEHICLE_ELIGIBILITY.value: frozenset({"key_quote", "offers", "selection"}),
    WorkflowState.PROFILING.value: frozenset({"key_quote", "offers", "selection"}),
    WorkflowState.SIMULATION.value: frozenset({"offers", "selection"}),
    WorkflowState.DOCUMENT_COLLECTION.value: frozenset(),
}


async def mark_ready_for_financial(
    session: AsyncSession, ctx: ExecutionContext, case: Case, now: datetime
) -> R.Verdict:
    """El caso debe venir bloqueado por el llamador. Devuelve el dictamen."""
    verdict = R.readiness(await gather_evidence(session, case, now), now)
    if verdict.all_pass:
        bump(case)
        transition(case, WorkflowState.READY_FOR_FINANCIAL)
        set_wait(case, WaitReason.NONE)
        case.ready_verdict = verdict.safe_evidence()
        await append_event(session, ctx, case, "CASE_READY", verdict.safe_evidence())
        return verdict

    recovery = verdict.recovery
    bump(case)
    await append_event(session, ctx, case, "READY_GATE_FAILED", verdict.safe_evidence())
    if recovery == R.NOT_APPLICABLE:
        return verdict
    if recovery in RECOVERY_INVALIDATIONS:
        # Etapa más temprana invalidada: se retrocede y se descartan las dependencias caducas.
        deps = RECOVERY_INVALIDATIONS[recovery]
        applied = await invalidate(session, case, deps, now)
        await invalidate_validation(session, case)
        transition(case, WorkflowState(recovery))
        set_wait(case, WaitReason.NONE)
        await append_event(
            session, ctx, case, "DEPENDENCIES_INVALIDATED",
            {"dependencies": applied + ["validation"], "from_state": "DOCUMENT_VALIDATION",
             "to_state": recovery, "reason": "READY_GATE_FAILED"},
        )  # fmt: skip
    elif recovery == R.REVALIDATE:
        await invalidate_validation(session, case)
    elif recovery == R.WAIT_CUSTOMER:
        set_wait(case, WaitReason.CUSTOMER_INPUT)
    elif recovery == R.WAIT_PROVIDER:
        set_wait(case, WaitReason.PROVIDER_UNKNOWN)
    return verdict
