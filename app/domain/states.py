from __future__ import annotations

import enum


class WorkflowState(enum.StrEnum):
    VEHICLE_ELIGIBILITY = "VEHICLE_ELIGIBILITY"
    PROFILING = "PROFILING"
    SIMULATION = "SIMULATION"
    DOCUMENT_COLLECTION = "DOCUMENT_COLLECTION"
    DOCUMENT_VALIDATION = "DOCUMENT_VALIDATION"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    READY_FOR_FINANCIAL = "READY_FOR_FINANCIAL"
    REJECTED = "REJECTED"


class WaitReason(enum.StrEnum):
    NONE = "NONE"
    CUSTOMER_INPUT = "CUSTOMER_INPUT"
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    PROVIDER_RETRY = "PROVIDER_RETRY"
    PROVIDER_UNKNOWN = "PROVIDER_UNKNOWN"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


TERMINAL_STATES = frozenset({WorkflowState.READY_FOR_FINANCIAL, WorkflowState.REJECTED})

# Orden del camino principal: se usa para decidir si una invalidación obliga a retroceder.
MAIN_PATH = (
    WorkflowState.VEHICLE_ELIGIBILITY,
    WorkflowState.PROFILING,
    WorkflowState.SIMULATION,
    WorkflowState.DOCUMENT_COLLECTION,
    WorkflowState.DOCUMENT_VALIDATION,
)


def stage_index(state: WorkflowState) -> int:
    """Posición en el camino principal; los estados laterales cuentan como el final."""
    return MAIN_PATH.index(state) if state in MAIN_PATH else len(MAIN_PATH)
