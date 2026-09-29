from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import ApiError
from app.config import Settings
from app.domain.clock import Clock
from app.observability.traces import Tracer
from app.providers.bureau import BureauProvider
from app.providers.extraction import ExtractionProvider
from app.providers.key_quotes import KeyQuoteProvider
from app.providers.offers import OfferProvider
from app.security.auth import Identity, parse_bearer, resolve_identity
from app.security.rate_limit import RateLimiter
from app.storage.interfaces import DocumentStore
from app.tools.context import ExecutionContext


@dataclass
class AppServices:
    sessionmaker: async_sessionmaker[AsyncSession]
    store: DocumentStore
    tracer: Tracer
    clock: Clock
    settings: Settings
    rate_limiter: RateLimiter
    bureau: BureauProvider
    key_provider: KeyQuoteProvider
    offer_provider: OfferProvider
    extractor: ExtractionProvider


def services(request: Request) -> AppServices:
    return request.app.state.services


async def current_identity(request: Request) -> Identity:
    token = parse_bearer(request.headers.get("authorization"))
    if token is None:
        raise ApiError(401, "UNAUTHENTICATED", "Credencial ausente o inválida.")
    svc = services(request)
    async with svc.sessionmaker() as session:
        identity = await resolve_identity(session, token)
    if identity is None:
        raise ApiError(401, "UNAUTHENTICATED", "Credencial ausente o inválida.")
    kind = "read" if request.method in ("GET", "HEAD") else "write"
    if not svc.rate_limiter.allow(f"{identity.actor_id}:{kind}", kind):
        raise ApiError(
            429, "RATE_LIMITED", "Demasiadas solicitudes; espera un momento.", retryable=True
        )
    return identity


async def execution_context(request: Request) -> ExecutionContext:
    identity = await current_identity(request)
    return ExecutionContext(
        actor_id=identity.actor_id,
        role=identity.role,
        customer_id=identity.customer_id,
        correlation_id=request.state.correlation_id,
    )


_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{8,128}$")


def idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(428, "IDEMPOTENCY_KEY_REQUIRED", "Falta la cabecera Idempotency-Key.")
    if not _KEY.fullmatch(key):
        raise ApiError(422, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key con formato inválido.")
    return key


def if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None:
        raise ApiError(428, "PRECONDITION_REQUIRED", "Falta la cabecera If-Match con la versión.")
    value = raw.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise ApiError(422, "INVALID_IF_MATCH", "If-Match debe ser la versión numérica del caso.")
    return int(value)
