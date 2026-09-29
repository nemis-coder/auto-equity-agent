from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.advisor_page import router as advisor_page_router
from app.api.agent_turns_routes import router as agent_turns_router
from app.api.cases_routes import router as cases_router
from app.api.deps import AppServices
from app.api.errors import install_error_handlers
from app.api.reviews_routes import router as reviews_router
from app.api.routes import router
from app.config import Settings, get_settings
from app.domain.clock import Clock, build_clock
from app.observability.context import parse_traceparent
from app.observability.traces import Tracer, build_tracer
from app.persistence.db import make_engine, make_sessionmaker
from app.providers.bureau import MockBureauProvider
from app.providers.extraction import (
    ExtractionProvider,
    FakeExtractionProvider,
    LLMExtractionProvider,
)
from app.providers.key_quotes import MockKeyQuoteProvider
from app.providers.llm import DEFAULT_MODEL, build_chat_model
from app.providers.offers import MockOfferProvider
from app.security.rate_limit import RateLimiter
from app.storage.interfaces import DocumentStore
from app.storage.s3 import S3DocumentStore

log = logging.getLogger("auto_equity")


def build_store(settings: Settings) -> DocumentStore:
    return S3DocumentStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        namespace=settings.s3_namespace,
    )


def build_extractor(settings: Settings) -> ExtractionProvider:
    if settings.extraction_provider != "fake":
        model = settings.extraction_model or DEFAULT_MODEL
        key = (settings.openai_api_key if settings.extraction_provider == "openai"
               else settings.anthropic_api_key)
        chat = build_chat_model(
            settings.extraction_provider,
            model,
            max_output_tokens=1500,
            api_key=key.get_secret_value().strip(),
        )
        return LLMExtractionProvider(
            chat, settings.extraction_provider, model, settings.max_document_pages
        )
    if settings.app_mode == "real_ai":
        raise ValueError("APP_MODE=real_ai no admite el extractor fake.")
    return FakeExtractionProvider()


def create_app(
    settings: Settings | None = None,
    *,
    store: DocumentStore | None = None,
    tracer: Tracer | None = None,
    clock: Clock | None = None,
    extractor: ExtractionProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        app.state.services = AppServices(
            sessionmaker=sessionmaker,
            bureau=MockBureauProvider(sessionmaker),
            key_provider=MockKeyQuoteProvider(sessionmaker),
            offer_provider=MockOfferProvider(sessionmaker),
            extractor=extractor or build_extractor(settings),
            store=store or build_store(settings),
            tracer=tracer or build_tracer(settings),
            clock=clock or build_clock(settings.clock_mode, settings.fixed_now),
            settings=settings,
            rate_limiter=RateLimiter(
                settings.rate_limit_per_minute, settings.read_rate_limit_per_minute
            ),
        )
        if not app.state.services.tracer.langfuse_enabled:
            log.warning("Telemetría externa desactivada (LANGFUSE_ENABLED=false).")
        try:
            yield
        finally:
            app.state.services.tracer.flush()
            await engine.dispose()

    app = FastAPI(title="Auto Equity Agent API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request: Request, call_next) -> Response:
        request.state.correlation_id = str(uuid.uuid4())
        if request.url.path in _UNTRACED_ROUTES:
            response = await call_next(request)
        else:
            tracer: Tracer = request.app.state.services.tracer
            with tracer.span(
                "http.request",
                trace_context=parse_traceparent(request.headers.get("traceparent", "")),
                metadata={
                    "correlation_id": request.state.correlation_id,
                    "route": f"{request.method}:{request.url.path}",
                },
            ) as span:
                response = await call_next(request)
                span.update(metadata={"http_status": response.status_code})
                if response.status_code >= 400:
                    span.update(level="ERROR")
                response.headers["X-Trace-Id"] = span.trace_id
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        return response

    app.include_router(router)
    app.include_router(cases_router)
    app.include_router(agent_turns_router)
    app.include_router(reviews_router)
    app.include_router(advisor_page_router)
    return app


# Salud y archivos estáticos de la pantalla del asesor: sin traza.
_UNTRACED_ROUTES = frozenset(
    {"/healthz", "/readyz", "/asesor", "/asesor/asesor.js", "/asesor/asesor.css"}
)
