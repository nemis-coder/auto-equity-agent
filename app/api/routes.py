from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import current_identity, services
from app.api.schemas import MeResponse, ReadyResponse
from app.persistence.models import ActorRole
from app.security.auth import Identity
from app.storage.interfaces import StorageError

router = APIRouter()

PERMISSIONS = {
    ActorRole.CUSTOMER: ["cases:own:read", "cases:own:write"],
    ActorRole.ADVISOR: ["reviews:assigned:read", "reviews:assigned:resolve"],
    ActorRole.SERVICE: ["cases:delegated:operate"],
}


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(request: Request) -> JSONResponse:
    svc = services(request)
    checks: dict[str, str] = {}
    try:
        async with svc.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - readiness solo informa estado
        checks["database"] = "unavailable"
    try:
        await svc.store.check_ready()
        checks["document_store"] = "ok"
    except StorageError:
        checks["document_store"] = "unavailable"
    # Configuración, no confirmación de recepción: eso lo comprueba trace_smoke.py.
    checks["telemetry"] = "langfuse_configured" if svc.tracer.langfuse_enabled else "local_only"
    if svc.tracer.export_errors:
        checks["telemetry"] = "export_error"
    ready = checks["database"] == "ok" and checks["document_store"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
            # Fecha de negocio (no secreta): los scripts de demo la comparan con FIXED_NOW
            # antes de usar documentos fechados (TDD §11.1).
            "clock": {
                "mode": svc.settings.clock_mode,
                "business_date": svc.clock.business_now().date().isoformat(),
            },
        },
    )


@router.get("/me", response_model=MeResponse)
async def me(identity: Annotated[Identity, Depends(current_identity)]) -> MeResponse:
    return MeResponse(
        actor_id=identity.actor_id,
        alias=identity.alias,
        role=identity.role.value,
        permissions=PERMISSIONS[identity.role],
    )
