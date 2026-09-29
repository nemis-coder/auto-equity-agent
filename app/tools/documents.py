"""Tools `attach_document`, `extract_document` y `validate_documents` (TDD §6.6, §6.9).

Carga, extracción y validación son pasos separados. Los bytes viven en el almacenamiento S3
bajo una clave inmutable derivada del contenido; PostgreSQL guarda metadatos y referencias.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents import (
    POLICY_VERSION,
    SLOTS,
    ActiveDocument,
    RuleResult,
    all_pass,
    evaluate,
    slot_of,
)
from app.domain.states import WaitReason, WorkflowState, stage_index
from app.persistence.models import (
    Case,
    Document,
    DocumentExtraction,
    Selection,
    Validation,
    Vehicle,
)
from app.providers.extraction import PROMPT_VERSION, ExtractionProvider, ExtractionUnavailable
from app.storage.interfaces import DocumentStore, StorageError
from app.tools.cases import append_event, bump, require_mutable, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.errors import ActionError, not_found

UPLOAD_STATES = frozenset(
    {
        WorkflowState.DOCUMENT_COLLECTION,
        WorkflowState.DOCUMENT_VALIDATION,
        WorkflowState.NEEDS_CORRECTION,
        WorkflowState.HUMAN_REVIEW,
    }
)
MAX_CORRECTION_ROUNDS = 2


def storage_key(case_id: uuid.UUID, sha256: str) -> str:
    # Derivada del contenido: un reintento reutiliza la misma clave y nunca se sobrescribe
    # evidencia distinta (TDD §6.9).
    return f"cases/{case_id}/documents/{sha256}"


async def store_bytes(store: DocumentStore, case_id: uuid.UUID, content: bytes) -> str:
    sha = hashlib.sha256(content).hexdigest()
    try:
        await store.put(storage_key(case_id, sha), content, sha)
    except StorageError as exc:
        raise ActionError(
            503, "STORAGE_UNAVAILABLE", "No pudimos guardar el archivo; reintenta.", retryable=True
        ) from exc
    return sha


async def active_documents(session: AsyncSession, case: Case) -> list[Document]:
    rows = await session.scalars(
        select(Document)
        .where(Document.case_id == case.id, Document.active.is_(True))
        .order_by(Document.slot)
    )
    return list(rows)


async def current_extraction(session: AsyncSession, doc: Document) -> DocumentExtraction | None:
    return await session.scalar(
        select(DocumentExtraction).where(
            DocumentExtraction.document_id == doc.id,
            DocumentExtraction.status == "CURRENT",
            DocumentExtraction.document_sha256 == doc.sha256,
        )
    )


async def invalidate_validation(session: AsyncSession, case: Case) -> bool:
    res = await session.execute(
        update(Validation)
        .where(Validation.case_id == case.id, Validation.active.is_(True))
        .values(active=False)
    )
    return bool(res.rowcount)


async def attach_document(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    *,
    kind: str,
    sha256: str,
    mime_type: str,
    size_bytes: int,
    page_count: int,
    supersedes_id: uuid.UUID | None,
) -> Document:
    require_mutable(case)
    state = WorkflowState(case.workflow_state)
    has_selection = await session.scalar(
        select(func.count())
        .select_from(Selection)
        .where(Selection.case_id == case.id, Selection.revoked_at.is_(None))
    )
    if state not in UPLOAD_STATES or not has_selection:
        raise ActionError(
            409, "INVALID_STATE", "Los documentos se cargan después de elegir una opción."
        )
    slot = slot_of(kind)
    previous = await session.scalar(
        select(Document).where(
            Document.case_id == case.id, Document.slot == slot, Document.active.is_(True)
        )
    )
    if supersedes_id is not None and (previous is None or previous.id != supersedes_id):
        raise ActionError(
            409, "DOCUMENT_NOT_CURRENT", "El documento a reemplazar ya no es el vigente."
        )
    revision = (
        await session.scalar(
            select(func.coalesce(func.max(Document.revision), 0)).where(
                Document.case_id == case.id, Document.slot == slot
            )
        )
        + 1
    )
    if previous is not None:
        previous.active = False
        await session.flush()
    document = Document(
        id=uuid.uuid4(),
        case_id=case.id,
        kind=kind,
        slot=slot,
        sha256=sha256,
        storage_key=storage_key(case.id, sha256),
        mime_type=mime_type,
        size_bytes=size_bytes,
        page_count=page_count,
        revision=revision,
        supersedes_id=previous.id if previous else None,
        active=True,
        uploaded_by=ctx.actor_id,
    )
    session.add(document)
    bump(case)
    case.advisor_request = None  # la carga atiende la solicitud del asesor
    await append_event(
        session,
        ctx,
        case,
        "DOCUMENT_ATTACHED",
        {"document_id": str(document.id), "kind": kind, "revision": revision},
    )
    if await invalidate_validation(session, case) or state in (
        WorkflowState.DOCUMENT_VALIDATION,
        WorkflowState.NEEDS_CORRECTION,
    ):
        await append_event(
            session, ctx, case, "DEPENDENCIES_INVALIDATED",
            {"dependencies": ["validation"], "from_state": state.value,
             "to_state": WorkflowState.DOCUMENT_COLLECTION.value
             if state is not WorkflowState.HUMAN_REVIEW else state.value},
        )  # fmt: skip
    if state in (WorkflowState.DOCUMENT_VALIDATION, WorkflowState.NEEDS_CORRECTION):
        transition(case, WorkflowState.DOCUMENT_COLLECTION)
    if state is not WorkflowState.HUMAN_REVIEW:
        set_wait(case, WaitReason.NONE)
    return document


async def load_document(session: AsyncSession, case: Case, document_id: uuid.UUID) -> Document:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.case_id == case.id)
    )
    if document is None:
        raise not_found()
    return document


# --- Extracción ------------------------------------------------------------------------------


async def pending_extractions(session: AsyncSession, case: Case) -> list[Document]:
    return [
        d for d in await active_documents(session, case) if not await current_extraction(session, d)
    ]


async def extract_document(
    sessionmaker,
    store: DocumentStore,
    provider: ExtractionProvider,
    origin: ExecutionContext,
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    token_budget=None,
    tracer=None,
) -> str:
    from app.tools.credit import service_context

    async with sessionmaker() as session:
        doc = await session.get(Document, document_id)
        if doc is None or doc.case_id != case_id or not doc.active:
            return "SKIPPED"
        key, sha, mime, kind, pages = (
            doc.storage_key, doc.sha256, doc.mime_type, doc.kind, doc.page_count
        )  # fmt: skip
    try:
        content = await store.get(key, sha)
    except StorageError:
        return "STORAGE_UNAVAILABLE"
    from app.tools.budget import TokenBudget, pause_for_budget, reserve, settle

    budget = token_budget or TokenBudget()
    # La extracción real puede hacer una segunda llamada si la primera no es JSON válido.
    reservation = await reserve(sessionmaker, case_id, budget, calls=2)
    if reservation is None:
        await pause_for_budget(sessionmaker, origin, case_id)
        return "BUDGET_EXCEEDED"
    used = (0, 0)
    span_cm = (
        tracer.span(
            "document.extraction",
            kind="generation",
            metadata={
                "document_kind": kind,
                "provider": getattr(provider, "name", None),
                "model": getattr(provider, "model", None),
                "prompt_version": PROMPT_VERSION,
            },
        )
        if tracer is not None
        else nullcontext(None)
    )
    with span_cm as span:
        try:
            result = await provider.extract(content, mime, kind, pages)
            used = (result.input_tokens, result.output_tokens)
        except ExtractionUnavailable as exc:
            if exc.usage:
                used = (exc.usage.get("input", 0), exc.usage.get("output", 0))
            if span is not None:
                span.update(
                    level="ERROR",
                    metadata={"outcome": "MODEL_UNAVAILABLE", "error_code": str(exc)},
                    usage=exc.usage,
                )
            async with sessionmaker() as session, session.begin():
                case = await session.scalar(
                    select(Case).where(Case.id == case_id).with_for_update()
                )
                svc_ctx = await service_context(session, origin)
                bump(case)
                set_wait(case, WaitReason.MODEL_UNAVAILABLE)
                await append_event(session, svc_ctx, case, "EXTRACTION_UNAVAILABLE",
                                   {"document_id": str(document_id)})  # fmt: skip
            return "MODEL_UNAVAILABLE"
        finally:
            await settle(sessionmaker, reservation, *used)
        if span is not None:
            span.update(
                metadata={
                    "outcome": "OK",
                    "provider": result.provider,
                    "model": result.model,
                    "schema_version": result.extraction["schema_version"],
                },
                usage=result.usage or ({
                    "input": result.input_tokens, "output": result.output_tokens,
                } if result.input_tokens or result.output_tokens or result.provider == "fake"
                    else None),
            )

    async with sessionmaker() as session, session.begin():
        case = await session.scalar(select(Case).where(Case.id == case_id).with_for_update())
        doc = await session.get(Document, document_id)
        svc_ctx = await service_context(session, origin)
        if not doc.active or doc.sha256 != sha:
            return "DISCARDED"  # el documento se reemplazó mientras se leía
        if await current_extraction(session, doc) is not None:
            return "ALREADY_EXTRACTED"
        session.add(
            DocumentExtraction(
                id=uuid.uuid4(),
                case_id=case.id,
                document_id=doc.id,
                document_sha256=sha,
                schema_version=result.extraction["schema_version"],
                provider=result.provider,
                model=result.model,
                prompt_version=PROMPT_VERSION,
                extraction=result.extraction,
                status="CURRENT",
            )
        )
        bump(case)
        await append_event(
            session, svc_ctx, case, "DOCUMENT_EXTRACTED",
            {"document_id": str(doc.id), "provider": result.provider, "model": result.model,
             "legible": result.extraction["legible"],
             "detected_type": result.extraction["detected_type"]},
        )  # fmt: skip
        return "OK"


# --- Validación ------------------------------------------------------------------------------


def validation_fingerprint(case: Case, docs: list[tuple[Document, DocumentExtraction]]) -> str:
    """Depende del contenido (hashes), no de ids: subir el mismo archivo no es evidencia nueva."""
    material = {
        "input_revision": case.input_revision,
        "documents": sorted(
            (
                d.slot,
                d.kind,
                d.sha256,
                hashlib.sha256(json.dumps(e.extraction, sort_keys=True).encode()).hexdigest(),
            )
            for d, e in docs
        ),
        "policy": POLICY_VERSION,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


async def run_validation(
    session: AsyncSession, ctx: ExecutionContext, case: Case, now: datetime
) -> list[RuleResult]:
    """Evalúa las reglas sobre los documentos activos; no llama a proveedores ni al modelo."""
    pairs = []
    by_slot: dict[str, ActiveDocument] = {}
    for doc in await active_documents(session, case):
        extraction = await current_extraction(session, doc)
        if extraction is not None:
            pairs.append((doc, extraction))
        by_slot[doc.slot] = ActiveDocument(
            str(doc.id), doc.kind, extraction.extraction if extraction else None
        )
    vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
    results = evaluate(by_slot, case.declared_profile, {"vehicle_ref": vehicle.vehicle_ref}, now)
    fingerprint = validation_fingerprint(case, pairs)
    await invalidate_validation(session, case)
    run_id = uuid.uuid4()
    for r in results:
        session.add(
            Validation(
                id=uuid.uuid4(),
                case_id=case.id,
                run_id=run_id,
                rule_id=r.rule_id,
                status=r.status.value,
                reason_code=r.reason_code,
                evidence_refs=list(r.refs),
                input_fingerprint=fingerprint,
                policy_version=POLICY_VERSION,
                active=True,
            )
        )
    passed = all_pass(results)
    bump(case)
    await append_event(
        session, ctx, case, "VALIDATION_COMPLETED",
        {"all_pass": passed, "failed": [r.rule_id for r in results if r.status.value != "PASS"],
         "fingerprint": fingerprint},
    )  # fmt: skip
    if passed:
        set_wait(case, WaitReason.NONE)
        return results

    # Solo una nueva revisión de evidencias o datos consume otra ronda (TDD §6.6).
    if case.last_failed_validation_fp != fingerprint:
        case.correction_rounds += 1
        case.last_failed_validation_fp = fingerprint
    failures = [
        {"rule_id": r.rule_id, "reason_code": r.reason_code, "refs": list(r.refs)}
        for r in results
        if r.status.value != "PASS"
    ]
    if case.correction_rounds >= MAX_CORRECTION_ROUNDS:
        from app.tools.reviews import open_review

        await open_review(
            session, ctx, case,
            reason_code="DOCUMENT_CORRECTIONS_EXHAUSTED",
            resume_state=WorkflowState.DOCUMENT_COLLECTION.value,
            details={"correction_rounds": case.correction_rounds, "failed":
                     [f["rule_id"] for f in failures]},
        )  # fmt: skip
    else:
        transition(case, WorkflowState.NEEDS_CORRECTION)
        set_wait(case, WaitReason.CUSTOMER_INPUT)
        await append_event(
            session, ctx, case, "CORRECTION_REQUESTED",
            {"round": case.correction_rounds, "failures": failures},
        )  # fmt: skip
    return results


async def active_validations(session: AsyncSession, case: Case) -> list[Validation]:
    rows = await session.scalars(
        select(Validation)
        .where(Validation.case_id == case.id, Validation.active.is_(True))
        .order_by(Validation.created_at, Validation.rule_id)
    )
    return list(rows)


def missing_slots(docs: list[Document]) -> list[str]:
    present = {d.slot for d in docs}
    return [s for s in SLOTS if s not in present]


def in_document_stage(case: Case) -> bool:
    return stage_index(WorkflowState(case.workflow_state)) >= stage_index(
        WorkflowState.DOCUMENT_COLLECTION
    )


def document_view(doc: Document, extraction: DocumentExtraction | None) -> dict[str, Any]:
    return {
        "document_id": str(doc.id),
        "kind": doc.kind,
        "slot": doc.slot,
        "revision": doc.revision,
        "mime_type": doc.mime_type,
        "page_count": doc.page_count,
        "status": "EXTRACTED" if extraction else "RECEIVED",
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
    }
