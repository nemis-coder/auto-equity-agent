from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.transitions import TransitionError
from app.tools.errors import ActionError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def _body(request: Request, code: str, message: str, retryable: bool) -> dict:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "correlation_id": getattr(request.state, "correlation_id", ""),
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content=_body(request, exc.code, exc.message, exc.retryable)
        )

    @app.exception_handler(ActionError)
    async def _action_error(request: Request, exc: ActionError) -> JSONResponse:
        body = _body(request, exc.code, exc.message, exc.retryable)
        if exc.current_version is not None:
            body["current_version"] = exc.current_version
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(TransitionError)
    async def _transition(request: Request, exc: TransitionError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_body(request, "INVALID_TRANSITION", "Transición no permitida.", False),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_body(request, "INVALID_REQUEST", "La solicitud no cumple el esquema.", False),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_body(request, "INTERNAL_ERROR", "Error interno.", True),
        )
