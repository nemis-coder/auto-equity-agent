"""Endpoints de casos, declaraciones y confirmaciones (TDD §6.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import execution_context, idempotency_key, if_match, services
from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import ActorRole, Case, CaseEvent, Customer, Operation, Vehicle
from app.tools.advance import advance
from app.tools.budget import TokenBudget
from app.tools.cases import (
    append_event,
    empty_draft,
    idempotent,
    list_cases,
    load_case,
    pick_advisor,
    require_role,
    require_version,
)
from app.tools.context import ExecutionContext
from app.tools.credit import Runtime, select_offer
from app.tools.declarations import confirm_declarations, propose_declarations
from app.tools.documents import attach_document, load_document, store_bytes
from app.tools.errors import ActionError
from app.tools.file_intake import inspect_upload
from app.tools.snapshot import build_snapshot
from app.tools.turns import active_turn, new_turn, turn_operation

router = APIRouter()
Ctx = Annotated[ExecutionContext, Depends(execution_context)]


class ProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal["vehicle", "profile"]
    fields: dict[str, Any] = Field(min_length=1)


class SelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: uuid.UUID
    displayed_offer_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConfirmationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_action_id: uuid.UUID
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _response(status: int, body: dict[str, Any], replay: bool = False) -> JSONResponse:
    headers = {"Idempotent-Replay": "true"} if replay else {}
    if "case_version" in body:
        headers["ETag"] = f'"{body["case_version"]}"'
    return JSONResponse(status_code=status, content=body, headers=headers)


def _summary(case: Case) -> dict[str, Any]:
    return {
        "case_id": str(case.id),
        "workflow_state": case.workflow_state,
        "wait_reason": case.wait_reason,
        "case_version": case.version,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


@router.get("/cases")
async def get_cases(
    request: Request,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    async with services(request).sessionmaker() as session:
        cases = await list_cases(session, ctx, limit=limit, offset=offset)
    return {"items": [_summary(c) for c in cases], "limit": limit, "offset": offset}


@router.post("/cases", status_code=201)
async def create_case(request: Request, ctx: Ctx) -> JSONResponse:
    require_role(ctx, ActorRole.CUSTOMER)
    key = idempotency_key(request)
    svc = services(request)
    now = svc.clock.business_now()

    async with svc.sessionmaker() as session, session.begin():

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            # Serializa las creaciones del cliente y reutiliza una solicitud sin empezar.
            await session.execute(
                select(Customer.id).where(Customer.id == ctx.customer_id).with_for_update()
            )
            draft = await empty_draft(session, ctx.customer_id)
            if draft is not None:
                draft.updated_at = datetime.now(UTC)  # sube al inicio de «Mis solicitudes»
                body = await build_snapshot(session, ctx, draft, now)
                body["reused_draft"] = True
                return 200, body
            ts = datetime.now(UTC)
            case = Case(
                id=uuid.uuid4(),
                customer_id=ctx.customer_id,
                assigned_advisor_id=await pick_advisor(session),
                workflow_state=WorkflowState.VEHICLE_ELIGIBILITY.value,
                wait_reason=WaitReason.CUSTOMER_INPUT.value,
                version=1,
                input_revision=0,
                policy_version=svc.settings.policy_version,
                declared_profile={},
                created_at=ts,
                updated_at=ts,
            )
            session.add(case)
            session.add(Vehicle(id=uuid.uuid4(), case_id=case.id))
            await session.flush()
            await append_event(
                session, ctx, case, "CASE_CREATED", {"policy_version": case.policy_version},
                operation_id=op_id,
            )  # fmt: skip
            body = await build_snapshot(session, ctx, case, now)
            body["reused_draft"] = False
            return 201, body

        status, body, replay = await idempotent(
            session, ctx, action="create_case", key=key, payload={}, case_id=None, execute=execute
        )
    return _response(status, body, replay)


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, request: Request, ctx: Ctx) -> JSONResponse:
    svc = services(request)
    async with svc.sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        body = await build_snapshot(session, ctx, case, svc.clock.business_now())
    return _response(200, body)


@router.post("/cases/{case_id}/declaration-proposals")
async def post_proposal(
    case_id: uuid.UUID, payload: ProposalIn, request: Request, ctx: Ctx
) -> JSONResponse:
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    now = svc.clock.business_now()
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            await propose_declarations(
                session, ctx, case, group=payload.group, fields=payload.fields, now=now,
                ttl_minutes=svc.settings.pending_action_ttl_minutes,
            )  # fmt: skip
            return 200, await build_snapshot(session, ctx, case, now)

        status, body, replay = await idempotent(
            session, ctx, action="propose_declarations", key=key,
            payload=payload.model_dump(mode="json"), case_id=case.id, execute=execute,
        )  # fmt: skip
    return _response(status, body, replay)


@router.post("/cases/{case_id}/confirmations")
async def post_confirmation(
    case_id: uuid.UUID, payload: ConfirmationIn, request: Request, ctx: Ctx
) -> JSONResponse:
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    now = svc.clock.business_now()
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            await confirm_declarations(
                session, ctx, case, pending_action_id=payload.pending_action_id,
                confirmed_hash=payload.payload_hash, now=now,
            )  # fmt: skip
            return 200, await build_snapshot(session, ctx, case, now)

        status, body, replay = await idempotent(
            session, ctx, action="confirm_declarations", key=key,
            payload=payload.model_dump(mode="json"), case_id=case.id, execute=execute,
        )  # fmt: skip
    if replay:
        return _response(status, body, replay)
    return await _advance_and_snapshot(request, ctx, case_id, retry_failed=False)


def _runtime(request: Request) -> Runtime:
    svc = services(request)
    return Runtime(
        sessionmaker=svc.sessionmaker,
        clock=svc.clock,
        bureau=svc.bureau,
        key_provider=svc.key_provider,
        offer_provider=svc.offer_provider,
        retry_delays=svc.settings.provider_retry_delays,
        provider_timeout=svc.settings.provider_timeout_seconds,
        store=svc.store,
        extractor=svc.extractor,
        token_budget=TokenBudget.from_settings(svc.settings),
        tracer=svc.tracer,
    )


async def _advance_and_snapshot(
    request: Request,
    ctx: ExecutionContext,
    case_id: uuid.UUID,
    *,
    retry_failed: bool,
    status: int = 200,
) -> JSONResponse:
    svc = services(request)
    await advance(_runtime(request), ctx, case_id, retry_failed=retry_failed)
    async with svc.sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        body = await build_snapshot(session, ctx, case, svc.clock.business_now())
    return _response(status, body)


@router.post("/cases/{case_id}/runs")
async def post_run(case_id: uuid.UUID, request: Request, ctx: Ctx) -> JSONResponse:
    """Reanuda desde el estado durable; no acepta acciones ni estados destino (TDD §6.3).

    Registra un turno `AGENT_TURN` con lease: un replay con la misma clave devuelve 202
    mientras el turno sigue vivo y lo reejecuta si el lease venció.
    """
    key = idempotency_key(request)
    svc = services(request)
    lease = timedelta(seconds=svc.settings.turn_timeout_seconds + 30)
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)
        real_now = datetime.now(UTC)
        turn = await turn_operation(session, ctx, f"run:{key}")
        if turn is not None and turn.case_id != case.id:
            raise ActionError(409, "IDEMPOTENCY_CONFLICT", "La clave ya se usó con otro caso.")
        if turn is not None and turn.status == "SUCCEEDED":
            body = await build_snapshot(session, ctx, case, svc.clock.business_now())
            return _response(200, body, replay=True)
        if turn is not None and turn.status == "PENDING" and turn.lease_expires_at > real_now:
            body = await build_snapshot(session, ctx, case, svc.clock.business_now())
            body["turn_status"] = "IN_PROGRESS"
            return _response(202, body)
        if await active_turn(session, case.id, real_now):
            raise ActionError(
                409, "TURN_IN_PROGRESS", "Ya estamos procesando tu solicitud.", retryable=True
            )
        if turn is None:
            turn = new_turn(ctx, case.id, f"run:{key}", "", real_now + lease)
            session.add(turn)
        else:
            turn.status, turn.attempts = "PENDING", turn.attempts + 1
            turn.lease_expires_at, turn.updated_at = real_now + lease, real_now
        turn_id = turn.id
    response = await _advance_and_snapshot(request, ctx, case_id, retry_failed=True)
    async with svc.sessionmaker() as session, session.begin():
        turn = await session.get(Operation, turn_id, with_for_update=True)
        turn.status, turn.lease_expires_at = "SUCCEEDED", None
        turn.http_status, turn.updated_at = 200, datetime.now(UTC)
    return response


@router.post("/cases/{case_id}/selections")
async def post_selection(
    case_id: uuid.UUID, payload: SelectionIn, request: Request, ctx: Ctx
) -> JSONResponse:
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    now = svc.clock.business_now()
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)
        require_role(ctx, ActorRole.CUSTOMER)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            await select_offer(
                session, ctx, case, offer_id=payload.offer_id,
                displayed_hash=payload.displayed_offer_hash, now=now,
            )  # fmt: skip
            return 200, await build_snapshot(session, ctx, case, now)

        status, body, replay = await idempotent(
            session, ctx, action="select_simulation", key=key,
            payload=payload.model_dump(mode="json"), case_id=case.id, execute=execute,
        )  # fmt: skip
    if replay:
        return _response(status, body, replay)
    # Si los documentos ya estaban (nueva elección tras una oferta vencida, D24), se revalidan
    # y pasan por el gate sin pedir otra carga.
    return await _advance_and_snapshot(request, ctx, case_id, retry_failed=False)


@router.get("/cases/{case_id}/events")
async def get_events(case_id: uuid.UUID, request: Request, ctx: Ctx) -> dict[str, Any]:
    async with services(request).sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        events = await session.scalars(
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id)
            .order_by(CaseEvent.case_version, CaseEvent.created_at)
        )
        detailed = ctx.role is ActorRole.ADVISOR
        items = [
            {
                "event_type": e.event_type,
                "case_version": e.case_version,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                **({"payload": e.safe_payload} if detailed else {}),
            }
            for e in events
        ]
    return {"case_id": str(case.id), "items": items}


DocumentKind = Literal["IDENTITY", "PAYSLIP", "INCOME_STATEMENT", "VEHICLE_OWNERSHIP"]


@router.post("/cases/{case_id}/documents")
async def post_document(
    case_id: uuid.UUID,
    request: Request,
    ctx: Ctx,
    file: Annotated[UploadFile, File()],
    declared_type: Annotated[DocumentKind, Form()],
    supersedes_id: Annotated[uuid.UUID | None, Form()] = None,
) -> JSONResponse:
    """Adjunta sin dar por leídos los bytes; la extracción y validación ocurren después."""
    key = idempotency_key(request)
    expected = if_match(request)
    svc = services(request)
    settings = svc.settings
    async with svc.sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        require_role(ctx, ActorRole.CUSTOMER, ActorRole.ADVISOR)
    content = await file.read(settings.max_file_bytes + 1)
    inspected = inspect_upload(
        content,
        max_bytes=settings.max_file_bytes,
        max_pages=settings.max_document_pages,
        max_pixels=settings.max_image_pixels,
    )
    # Primero los bytes (clave inmutable por contenido), luego los metadatos. Un fallo de la
    # base deja a lo sumo un objeto huérfano, nunca un documento sin bytes (TDD §6.9).
    sha = await store_bytes(svc.store, case.id, content)
    now = svc.clock.business_now()
    async with svc.sessionmaker() as session, session.begin():
        case = await load_case(session, ctx, case_id, for_update=True)

        async def execute(op_id: uuid.UUID) -> tuple[int, dict[str, Any]]:
            require_version(case, expected)
            await attach_document(
                session, ctx, case, kind=declared_type, sha256=sha,
                mime_type=inspected.mime_type, size_bytes=len(content),
                page_count=inspected.page_count, supersedes_id=supersedes_id,
            )  # fmt: skip
            body = await build_snapshot(session, ctx, case, now)
            body["reused_draft"] = False
            return 201, body

        status, body, replay = await idempotent(
            session, ctx, action="attach_document", key=key,
            payload={"sha256": sha, "declared_type": declared_type,
                     "supersedes_id": str(supersedes_id) if supersedes_id else None},
            case_id=case.id, execute=execute,
        )  # fmt: skip
    if replay:
        return _response(status, body, replay)
    return await _advance_and_snapshot(request, ctx, case_id, retry_failed=False, status=201)


@router.get("/cases/{case_id}/documents/{document_id}/content")
async def get_document_content(
    case_id: uuid.UUID, document_id: uuid.UUID, request: Request, ctx: Ctx
) -> Response:
    svc = services(request)
    async with svc.sessionmaker() as session:
        case = await load_case(session, ctx, case_id, for_update=False)
        document = await load_document(session, case, document_id)
        key, sha, mime = document.storage_key, document.sha256, document.mime_type
    from app.storage.interfaces import StorageError

    try:
        content = await svc.store.get(key, sha)
    except StorageError as exc:
        raise ActionError(
            503, "STORAGE_UNAVAILABLE", "Documento no disponible.", retryable=True
        ) from exc
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="documento-{document_id}.{extension}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
