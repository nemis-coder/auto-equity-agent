"""Perfilamiento, cotización de llave, ofertas y selección (TDD §6.4–6.5, §6.9, §14).

Las llamadas a proveedores siguen el protocolo de §6.9:
1. transacción corta: autorizar, verificar estado y registrar la operación con clave lógica;
2. llamada fuera del bloqueo del caso, con reintentos acotados y la misma clave;
3. transacción final: validar la respuesta y comprobar que las dependencias sigan vigentes
   antes de aplicarla; si no, se conserva para auditoría sin aplicarla.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.clock import Clock
from app.domain.declarations import bureau_fingerprint, missing_profile_fields, monthly_income
from app.domain.policy import MalformedBureauResponse, derive_profile, load_policy
from app.domain.simulation import InvalidOffers, offer_hash, parse_offers
from app.domain.states import WaitReason, WorkflowState
from app.persistence.models import (
    Actor,
    ActorRole,
    Case,
    CreditProfile,
    KeyQuote,
    Operation,
    Selection,
    Simulation,
    Vehicle,
)
from app.providers.base import ProviderError
from app.providers.bureau import BureauProvider
from app.providers.key_quotes import KeyQuoteProvider
from app.providers.offers import OfferProvider
from app.tools.cases import append_event, bump, require_mutable, set_wait, transition
from app.tools.context import ExecutionContext
from app.tools.errors import ActionError, not_found

VEHICLE_IDENTITY = ("vehicle_ref", "make", "model", "year")


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def vehicle_fingerprint(vehicle: Vehicle) -> str:
    return _sha({name: getattr(vehicle, name) for name in VEHICLE_IDENTITY})


# --- Consultas de vigencia (TDD §6.1, "Vigencia de perfil y cotización") ------------------


async def current_profile(session: AsyncSession, case: Case, now: datetime) -> CreditProfile | None:
    if missing_profile_fields(case.declared_profile):
        return None
    fingerprint = bureau_fingerprint(case.declared_profile)
    return await session.scalar(
        select(CreditProfile)
        .where(
            CreditProfile.case_id == case.id,
            CreditProfile.status.in_(("OK", "REVIEW")),
            CreditProfile.request_fingerprint == fingerprint,
            CreditProfile.policy_version == case.policy_version,
            CreditProfile.expires_at > now,
        )
        .order_by(CreditProfile.obtained_at.desc(), CreditProfile.created_at.desc())
        .limit(1)
    )


async def current_quote(
    session: AsyncSession, case: Case, vehicle: Vehicle, now: datetime
) -> KeyQuote | None:
    return await session.scalar(
        select(KeyQuote)
        .where(
            KeyQuote.case_id == case.id,
            KeyQuote.vehicle_id == vehicle.id,
            KeyQuote.status == "VALID",
            KeyQuote.request_fingerprint == vehicle_fingerprint(vehicle),
            KeyQuote.expires_at > now,
        )
        .order_by(KeyQuote.issued_at.desc(), KeyQuote.created_at.desc())
        .limit(1)
    )


async def active_offers(session: AsyncSession, case: Case) -> list[Simulation]:
    rows = await session.scalars(
        select(Simulation)
        .where(Simulation.case_id == case.id, Simulation.status == "ACTIVE")
        .order_by(Simulation.cash_amount, Simulation.term_months)
    )
    return list(rows)


async def active_selection(session: AsyncSession, case: Case) -> Selection | None:
    return await session.scalar(
        select(Selection).where(Selection.case_id == case.id, Selection.revoked_at.is_(None))
    )


def key_required(case: Case) -> bool:
    return bool((case.eligibility or {}).get("key_quote_required"))


# --- Invalidaciones (aplicadas por confirm_declarations) ----------------------------------


async def invalidate(
    session: AsyncSession, case: Case, deps: frozenset[str], now: datetime
) -> list[str]:
    applied: list[str] = []
    if "key_quote" in deps:
        res = await session.execute(
            update(KeyQuote)
            .where(KeyQuote.case_id == case.id, KeyQuote.status == "VALID")
            .values(status="SUPERSEDED")
        )
        if res.rowcount:
            applied.append("key_quote")
    if "offers" in deps:
        res = await session.execute(
            update(Simulation)
            .where(Simulation.case_id == case.id, Simulation.status == "ACTIVE")
            .values(status="SUPERSEDED")
        )
        if res.rowcount or case.simulation_result is not None:
            applied.append("offers")
        case.simulation_result = None
    if "selection" in deps:
        res = await session.execute(
            update(Selection)
            .where(Selection.case_id == case.id, Selection.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        if res.rowcount:
            applied.append("selection")
    return applied


# --- Simulación y selección ----------------------------------------------------------------


def _simulation_fingerprint(case: Case, profile: CreditProfile, quote: KeyQuote | None) -> str:
    """Entradas de las ofertas: perfil, cotización de llave, ingreso y política. El cliente no
    declara monto: las opciones las decide el cotizador (decisión 0038)."""
    return _sha(
        {
            "profile_id": str(profile.id),
            "quote_id": str(quote.id) if quote else None,
            "income": case.declared_profile["income"],
            "policy_version": case.policy_version,
        }
    )


def _offers_request(case: Case, profile: CreditProfile, quote: KeyQuote | None) -> dict[str, Any]:
    return {
        "annual_nominal_rate": str(profile.annual_nominal_rate),
        "max_financed_principal": str(profile.max_financed_principal),
        "monthly_net_income": str(monthly_income(case.declared_profile["income"])),
        "key_cost": str(quote.amount if quote else Decimal("0.00")),
        "currency": "MXN",
    }


def _store_offers(
    session: AsyncSession, case: Case, profile: CreditProfile, quote: KeyQuote | None,
    fingerprint: str, offers: tuple,
) -> list[Simulation]:  # fmt: skip
    batch, rows = uuid.uuid4(), []
    for offer in offers:
        display = offer.display()
        row = Simulation(
            id=uuid.uuid4(),
            case_id=case.id,
            batch_id=batch,
            profile_id=profile.id,
            quote_id=quote.id if quote else None,
            input_fingerprint=fingerprint,
            cash_amount=offer.cash_amount,
            key_cost=offer.key_cost,
            financed_principal=offer.financed_principal,
            annual_nominal_rate=offer.annual_nominal_rate,
            term_months=offer.term_months,
            regular_payment=offer.regular_payment,
            last_payment=offer.last_payment,
            total_payment=offer.total_payment,
            schedule=list(offer.schedule),
            display=display,
            display_hash=offer_hash(display),
            expires_at=offer.expires_at,
            status="ACTIVE",
            policy_version=case.policy_version,
        )
        session.add(row)
        rows.append(row)
    return rows


async def request_offers(
    rt: Runtime, origin: ExecutionContext, case_id: uuid.UUID, *, retry_failed: bool
) -> str:
    """Pide las opciones al cotizador de la financiera (protocolo de tres fases, §6.9)."""
    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        if case.workflow_state != WorkflowState.SIMULATION.value:
            return "SKIPPED"
        now = rt.clock.business_now()
        profile = await current_profile(session, case, now)
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
        quote = await current_quote(session, case, vehicle, now) if key_required(case) else None
        if profile is None or profile.outcome != "OK" or (key_required(case) and quote is None):
            return "SKIPPED"
        svc_ctx = await service_context(session, origin)
        fingerprint = _simulation_fingerprint(case, profile, quote)
        renewals = await session.scalar(
            select(func.count(func.distinct(Simulation.batch_id))).where(
                Simulation.case_id == case.id,
                Simulation.input_fingerprint == fingerprint,
                Simulation.status != "ACTIVE",
            )
        )
        prepared = await _prepare_operation(
            session, svc_ctx, case,
            action="quote_offers", input_fp=fingerprint,
            request=_offers_request(case, profile, quote), retry_failed=retry_failed,
            lease_seconds=rt.provider_timeout * (len(rt.retry_delays) + 2), renewals=renewals,
        )  # fmt: skip
    if prepared is None:
        return "WAITING"

    now = rt.clock.business_now()
    response, status = prepared.stored, "OK"
    if response is None:
        response, status = await _call_with_retries(
            lambda: rt.offer_provider.quote_offers(prepared.request, prepared.key, now),
            prepared.key,
            rt.offer_provider.lookup,
            rt.retry_delays,
            rt.provider_timeout,
        )

    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        op = await session.get(Operation, prepared.operation_id, with_for_update=True)
        svc_ctx = await service_context(session, origin)
        if status != "OK":
            await _finish_failure(
                session, svc_ctx, case, op, status, resume_state=WorkflowState.SIMULATION.value
            )
            return status
        op.status, op.result, op.lease_expires_at = "SUCCEEDED", response, None
        op.provider_ref = str(response.get("provider_ref", ""))[:128]
        op.updated_at = datetime.now(UTC)
        now = rt.clock.business_now()
        profile = await current_profile(session, case, now)
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
        quote = await current_quote(session, case, vehicle, now) if key_required(case) else None
        if (
            case.workflow_state != WorkflowState.SIMULATION.value
            or profile is None
            or _simulation_fingerprint(case, profile, quote) != prepared.input_fp
        ):
            bump(case)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESULT_DISCARDED",
                {"action": op.action, "reason": "DEPENDENCIES_CHANGED"}, operation_id=op.id,
            )  # fmt: skip
            return "DISCARDED"
        policy = load_policy(case.policy_version)
        capacity = monthly_income(case.declared_profile["income"]) * (
            policy.max_payment_to_income_ratio
        )
        try:
            valid_until = datetime.fromisoformat(response["valid_until"])
            offers = parse_offers(
                response,
                key_cost=quote.amount if quote else Decimal("0.00"),
                annual_rate=profile.annual_nominal_rate,
                max_financed_principal=profile.max_financed_principal,
                payment_capacity=capacity,
                expires_at=min(
                    [valid_until, profile.expires_at] + ([quote.expires_at] if quote else [])
                ),
            )
        except (KeyError, TypeError, ValueError, InvalidOffers):
            op.status = "FAILED_FINAL"
            bump(case)
            set_wait(case, WaitReason.PROVIDER_RETRY)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESPONSE_INVALID",
                {"action": op.action}, operation_id=op.id,
            )  # fmt: skip
            return "MALFORMED"
        rows = _store_offers(session, case, profile, quote, prepared.input_fp, offers)
        case.simulation_result = {"input_fingerprint": prepared.input_fp, "offers": len(rows)}
        bump(case)
        await append_event(
            session, svc_ctx, case, "OFFERS_GENERATED",
            {"count": len(rows), "terms": sorted({r.term_months for r in rows}),
             "key_cost": str(quote.amount if quote else Decimal("0.00"))}, operation_id=op.id,
        )  # fmt: skip
        if rows:
            set_wait(case, WaitReason.CUSTOMER_INPUT)
        else:
            # Sin opciones del cotizador: no es un rechazo; un asesor revisa el caso.
            from app.tools.reviews import open_review

            await open_review(
                session, svc_ctx, case,
                reason_code="NO_OFFERS_AVAILABLE", resume_state=WorkflowState.SIMULATION.value,
            )  # fmt: skip
        return "OK"


async def select_offer(
    session: AsyncSession,
    ctx: ExecutionContext,
    case: Case,
    *,
    offer_id: uuid.UUID,
    displayed_hash: str,
    now: datetime,
) -> Selection:
    require_mutable(case)
    offer = await session.scalar(
        select(Simulation).where(Simulation.id == offer_id, Simulation.case_id == case.id)
    )
    if offer is None:
        raise not_found()
    if case.workflow_state != WorkflowState.SIMULATION.value:
        raise ActionError(409, "INVALID_STATE", "Solo se elige una oferta en la simulación.")
    if offer.status != "ACTIVE":
        raise ActionError(409, "OFFER_NOT_CURRENT", "Esta oferta ya no está vigente.")
    if offer.expires_at <= now:
        raise ActionError(409, "OFFER_EXPIRED", "La oferta venció; recalcularemos las opciones.")
    if offer.display_hash != displayed_hash:
        raise ActionError(
            409, "OFFER_HASH_MISMATCH", "La oferta mostrada no coincide con la vigente."
        )
    profile = await current_profile(session, case, now)
    if profile is None or profile.id != offer.profile_id:
        raise ActionError(409, "OFFER_NOT_CURRENT", "El perfil de la oferta ya no está vigente.")
    if key_required(case):
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
        quote = await current_quote(session, case, vehicle, now)
        if quote is None or quote.id != offer.quote_id or quote.amount != offer.key_cost:
            raise ActionError(
                409, "OFFER_NOT_CURRENT", "La cotización de la llave ya no está vigente."
            )
    elif offer.key_cost != 0:
        raise ActionError(409, "OFFER_NOT_CURRENT", "La oferta incluye una llave que ya no aplica.")
    selection = Selection(
        id=uuid.uuid4(),
        case_id=case.id,
        offer_id=offer.id,
        actor_id=ctx.actor_id,
        displayed_offer_hash=displayed_hash,
        selected_at=now,
    )
    session.add(selection)
    bump(case)
    await append_event(
        session,
        ctx,
        case,
        "OFFER_SELECTED",
        {
            "offer_id": str(offer.id),
            "term_months": offer.term_months,
            "key_cost": str(offer.key_cost),
            "financed_principal": str(offer.financed_principal),
        },
    )
    transition(case, WorkflowState.DOCUMENT_COLLECTION)
    set_wait(case, WaitReason.DOCUMENT_UPLOAD)
    await append_event(
        session, ctx, case, "STAGE_ENTERED", {"stage": WorkflowState.DOCUMENT_COLLECTION.value}
    )
    return selection


# --- Proveedores ---------------------------------------------------------------------------


@dataclass
class Runtime:
    sessionmaker: async_sessionmaker[AsyncSession]
    clock: Clock
    bureau: BureauProvider
    key_provider: KeyQuoteProvider
    offer_provider: OfferProvider
    retry_delays: list[float]
    provider_timeout: float
    store: Any = None
    extractor: Any = None
    token_budget: Any = None  # TokenBudget; None = valores por defecto de S10
    tracer: Any = None  # Tracer; None = sin trazas (pruebas unitarias)


async def service_context(session: AsyncSession, origin: ExecutionContext) -> ExecutionContext:
    """Identidad del agente de servicio delegada por el actor de origen (TDD §9.1)."""
    service_id = await session.scalar(
        select(Actor.id)
        .where(Actor.role == ActorRole.SERVICE, Actor.active.is_(True))
        .order_by(Actor.alias)
        .limit(1)
    )
    if service_id is None:
        raise ActionError(
            503, "SERVICE_ACTOR_MISSING", "Falta el actor de servicio.", retryable=True
        )
    return ExecutionContext(
        actor_id=service_id,
        role=ActorRole.SERVICE,
        customer_id=origin.customer_id,
        correlation_id=origin.correlation_id,
        delegated_for=origin.actor_id,
    )


@dataclass
class _Prepared:
    operation_id: uuid.UUID
    key: str
    request: dict[str, Any]
    input_fp: str
    stored: dict[str, Any] | None


async def _prepare_operation(
    session: AsyncSession,
    svc_ctx: ExecutionContext,
    case: Case,
    *,
    action: str,
    input_fp: str,
    request: dict[str, Any],
    retry_failed: bool,
    lease_seconds: float,
    renewals: int,
) -> _Prepared | None:
    """Crea o recupera la operación lógica. None = no ejecutar ahora.

    La generación cambia solo tras un fallo final o una renovación (resultado vencido o
    invalidado), nunca por un reintento: así un perfil vencido obliga a consultar de nuevo.
    """
    failures = await session.scalar(
        select(func.count())
        .select_from(Operation)
        .where(
            Operation.case_id == case.id,
            Operation.action == action,
            Operation.input_hash == input_fp,
            Operation.status == "FAILED_FINAL",
        )
    )
    generation = failures + renewals
    key = f"{action}:" + _sha(
        {
            "case_id": str(case.id),
            "input": input_fp,
            "policy": case.policy_version,
            "generation": generation,
        }
    )
    op = await session.scalar(
        select(Operation)
        .where(Operation.actor_id == svc_ctx.actor_id, Operation.idempotency_key == key)
        .with_for_update()
    )
    real_now = datetime.now(UTC)
    if op is not None:
        if op.status == "SUCCEEDED":
            return _Prepared(op.id, key, request, input_fp, op.result)
        if op.status == "UNKNOWN":
            return None
        if op.status == "PENDING" and op.lease_expires_at and op.lease_expires_at > real_now:
            return None
        if op.status == "FAILED_RETRYABLE" and not retry_failed:
            return None
        op.status = "PENDING"
        op.attempts += 1
        op.lease_expires_at = real_now + timedelta(seconds=lease_seconds)
        op.updated_at = real_now
        return _Prepared(op.id, key, request, input_fp, None)
    op = Operation(
        id=uuid.uuid4(),
        actor_id=svc_ctx.actor_id,
        case_id=case.id,
        action=action,
        idempotency_key=key,
        input_hash=input_fp,
        status="PENDING",
        attempts=1,
        lease_expires_at=real_now + timedelta(seconds=lease_seconds),
    )
    session.add(op)
    return _Prepared(op.id, key, request, input_fp, None)


async def _call_with_retries(call, key: str, lookup, delays: list[float], timeout: float):
    """Devuelve (respuesta, status). status: OK | FAILED_RETRYABLE | UNKNOWN."""
    attempts = len(delays) + 1
    effect_possible = False
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(call(), timeout=timeout), "OK"
        except TimeoutError:
            effect_possible = True
        except ProviderError as exc:
            if not exc.retryable:
                raise
            effect_possible = effect_possible or exc.effect_may_have_happened
        if attempt < attempts - 1:
            await asyncio.sleep(delays[attempt])
    if effect_possible:
        found = await lookup(key)
        if found is not None:
            return found, "OK"
        return None, "UNKNOWN"
    return None, "FAILED_RETRYABLE"


async def _finish_failure(
    session: AsyncSession,
    svc_ctx: ExecutionContext,
    case: Case,
    op: Operation,
    status: str,
    resume_state: str = WorkflowState.PROFILING.value,
) -> None:
    op.status = status
    op.lease_expires_at = None
    op.updated_at = datetime.now(UTC)
    bump(case)
    if status == "UNKNOWN":
        await append_event(
            session,
            svc_ctx,
            case,
            "PROVIDER_OPERATION_UNKNOWN",
            {"action": op.action, "operation_id": str(op.id)},
            operation_id=op.id,
        )
        from app.tools.reviews import open_review

        await open_review(
            session, svc_ctx, case,
            reason_code="PROVIDER_UNKNOWN", resume_state=resume_state,
            details={"operation_id": str(op.id), "action": op.action},
            wait=WaitReason.PROVIDER_UNKNOWN,
        )  # fmt: skip
    else:
        set_wait(case, WaitReason.PROVIDER_RETRY)
        await append_event(
            session,
            svc_ctx,
            case,
            "PROVIDER_CALL_FAILED",
            {"action": op.action, "status": status},
            operation_id=op.id,
        )


async def _lock(session: AsyncSession, case_id: uuid.UUID) -> Case:
    return await session.scalar(select(Case).where(Case.id == case_id).with_for_update())


def _bureau_request(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": profile["full_name"],
        "address": profile["address"],
        "employment": profile["employment"],
        "monthly_net_income": str(monthly_income(profile["income"])),
        "currency": profile["income"]["currency"],
    }


async def query_credit_bureau(
    rt: Runtime, origin: ExecutionContext, case_id: uuid.UUID, *, retry_failed: bool
) -> str:
    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        if case.workflow_state != WorkflowState.PROFILING.value:
            return "SKIPPED"
        if missing_profile_fields(case.declared_profile):
            return "SKIPPED"
        svc_ctx = await service_context(session, origin)
        fingerprint = bureau_fingerprint(case.declared_profile)
        expired = await session.scalar(
            select(func.count())
            .select_from(CreditProfile)
            .where(
                CreditProfile.case_id == case.id,
                CreditProfile.request_fingerprint == fingerprint,
                CreditProfile.expires_at <= rt.clock.business_now(),
            )
        )
        prepared = await _prepare_operation(
            session, svc_ctx, case,
            action="query_credit_bureau", input_fp=fingerprint,
            request=_bureau_request(case.declared_profile), retry_failed=retry_failed,
            lease_seconds=rt.provider_timeout * (len(rt.retry_delays) + 2), renewals=expired,
        )  # fmt: skip
    if prepared is None:
        return "WAITING"

    response, status = prepared.stored, "OK"
    if response is None:
        response, status = await _call_with_retries(
            lambda: rt.bureau.query(prepared.request, prepared.key),
            prepared.key,
            rt.bureau.lookup,
            rt.retry_delays,
            rt.provider_timeout,
        )

    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        op = await session.get(Operation, prepared.operation_id, with_for_update=True)
        svc_ctx = await service_context(session, origin)
        if status != "OK":
            await _finish_failure(session, svc_ctx, case, op, status)
            return status
        op.status, op.result, op.lease_expires_at = "SUCCEEDED", response, None
        op.provider_ref = str(response.get("provider_ref", ""))[:128]
        op.updated_at = datetime.now(UTC)
        policy = load_policy(case.policy_version)
        try:
            derived = derive_profile(policy, response)
        except MalformedBureauResponse:
            op.status = "FAILED_FINAL"
            bump(case)
            set_wait(case, WaitReason.PROVIDER_RETRY)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESPONSE_INVALID",
                {"action": op.action}, operation_id=op.id,
            )  # fmt: skip
            return "MALFORMED"
        still_current = (
            case.workflow_state == WorkflowState.PROFILING.value
            and not missing_profile_fields(case.declared_profile)
            and bureau_fingerprint(case.declared_profile) == prepared.input_fp
        )
        if not still_current:
            bump(case)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESULT_DISCARDED",
                {"action": op.action, "reason": "DEPENDENCIES_CHANGED"}, operation_id=op.id,
            )  # fmt: skip
            return "DISCARDED"
        now = rt.clock.business_now()
        session.add(
            CreditProfile(
                id=uuid.uuid4(),
                case_id=case.id,
                operation_id=op.id,
                request_fingerprint=prepared.input_fp,
                provider_ref=op.provider_ref,
                score=response["score"],
                history_summary=response["history_summary"],
                conditions=response["conditions"],
                outcome=derived.outcome.value,
                reason_code=derived.reason_code,
                band=derived.band,
                annual_nominal_rate=derived.annual_nominal_rate,
                max_financed_principal=derived.max_financed_principal,
                obtained_at=now,
                expires_at=now + timedelta(days=policy.profile_validity_days),
                status=derived.outcome.value,
                policy_version=policy.version,
            )
        )
        bump(case)
        set_wait(case, WaitReason.NONE)
        await append_event(
            session, svc_ctx, case, "BUREAU_QUERIED",
            {"outcome": derived.outcome.value, "reason_code": derived.reason_code,
             "band": derived.band}, operation_id=op.id,
        )  # fmt: skip
        return "OK"


def _validate_quote(response: dict[str, Any], vehicle_ref: str, now: datetime) -> dict[str, Any]:
    try:
        amount = Decimal(str(response["amount"]))
        issued = datetime.fromisoformat(response["issued_at"])
        expires = datetime.fromisoformat(response["expires_at"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise MalformedBureauResponse("quote") from exc
    if (
        amount < 0
        or amount != amount.quantize(Decimal("0.01"))
        or response.get("currency") != "MXN"
        or response.get("vehicle_ref") != vehicle_ref
        or not issued < expires
        or expires <= now
        or not response.get("quote_id")
    ):
        raise MalformedBureauResponse("quote")
    return {"amount": amount, "issued_at": issued, "expires_at": expires}


async def quote_second_key(
    rt: Runtime, origin: ExecutionContext, case_id: uuid.UUID, *, retry_failed: bool
) -> str:
    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        if case.workflow_state != WorkflowState.PROFILING.value or not key_required(case):
            return "SKIPPED"
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.case_id == case.id))
        if any(getattr(vehicle, f) is None for f in VEHICLE_IDENTITY):
            return "SKIPPED"
        svc_ctx = await service_context(session, origin)
        request = {name: getattr(vehicle, name) for name in VEHICLE_IDENTITY}
        fingerprint = vehicle_fingerprint(vehicle)
        renewals = await session.scalar(
            select(func.count())
            .select_from(KeyQuote)
            .where(
                KeyQuote.case_id == case.id,
                KeyQuote.request_fingerprint == fingerprint,
                (KeyQuote.status == "SUPERSEDED")
                | (KeyQuote.expires_at <= rt.clock.business_now()),
            )
        )
        prepared = await _prepare_operation(
            session, svc_ctx, case,
            action="quote_second_key", input_fp=fingerprint,
            request=request, retry_failed=retry_failed,
            lease_seconds=rt.provider_timeout * (len(rt.retry_delays) + 2), renewals=renewals,
        )  # fmt: skip
        vehicle_id, vehicle_ref = vehicle.id, vehicle.vehicle_ref
    if prepared is None:
        return "WAITING"

    now = rt.clock.business_now()
    response, status = prepared.stored, "OK"
    if response is None:
        response, status = await _call_with_retries(
            lambda: rt.key_provider.quote(prepared.request, prepared.key, now),
            prepared.key,
            rt.key_provider.lookup,
            rt.retry_delays,
            rt.provider_timeout,
        )

    async with rt.sessionmaker() as session, session.begin():
        case = await _lock(session, case_id)
        op = await session.get(Operation, prepared.operation_id, with_for_update=True)
        svc_ctx = await service_context(session, origin)
        if status != "OK":
            await _finish_failure(session, svc_ctx, case, op, status)
            return status
        op.status, op.result, op.lease_expires_at = "SUCCEEDED", response, None
        op.provider_ref = str(response.get("provider_ref", ""))[:128]
        op.updated_at = datetime.now(UTC)
        try:
            valid = _validate_quote(response, vehicle_ref, rt.clock.business_now())
        except MalformedBureauResponse:
            op.status = "FAILED_FINAL"
            bump(case)
            set_wait(case, WaitReason.PROVIDER_RETRY)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESPONSE_INVALID",
                {"action": op.action}, operation_id=op.id,
            )  # fmt: skip
            return "MALFORMED"
        vehicle = await session.get(Vehicle, vehicle_id)
        if (
            case.workflow_state != WorkflowState.PROFILING.value
            or not key_required(case)
            or vehicle_fingerprint(vehicle) != prepared.input_fp
        ):
            bump(case)
            await append_event(
                session, svc_ctx, case, "PROVIDER_RESULT_DISCARDED",
                {"action": op.action, "reason": "DEPENDENCIES_CHANGED"}, operation_id=op.id,
            )  # fmt: skip
            return "DISCARDED"
        session.add(
            KeyQuote(
                id=uuid.uuid4(),
                case_id=case.id,
                vehicle_id=vehicle.id,
                operation_id=op.id,
                request_fingerprint=prepared.input_fp,
                quote_ref=response["quote_id"],
                provider_ref=op.provider_ref,
                amount=valid["amount"],
                currency="MXN",
                issued_at=valid["issued_at"],
                expires_at=valid["expires_at"],
                status="VALID",
            )
        )
        bump(case)
        set_wait(case, WaitReason.NONE)
        await append_event(
            session, svc_ctx, case, "KEY_QUOTED",
            {"amount": str(valid["amount"]), "currency": "MXN"}, operation_id=op.id,
        )  # fmt: skip
        return "OK"
