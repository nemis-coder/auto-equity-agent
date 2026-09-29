from __future__ import annotations


class ActionError(Exception):
    """Error de negocio o de contrato con código estable (catálogo de TDD §6.3)."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.current_version = current_version


def not_found() -> ActionError:
    # Mismo error para "no existe" y "no autorizado": no se filtra la existencia (TDD §6.3).
    return ActionError(404, "NOT_FOUND", "Recurso no encontrado.")


def forbidden() -> ActionError:
    return ActionError(403, "FORBIDDEN", "Tu rol no permite esta acción en este caso.")
