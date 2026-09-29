"""Transiciones permitidas (TDD §4.4). Ningún actor ni modelo asigna un estado arbitrario."""

from __future__ import annotations

from app.domain.states import WorkflowState

S = WorkflowState

ALLOWED: dict[WorkflowState, frozenset[WorkflowState]] = {
    S.VEHICLE_ELIGIBILITY: frozenset({S.PROFILING, S.REJECTED, S.HUMAN_REVIEW}),
    S.PROFILING: frozenset({S.SIMULATION, S.HUMAN_REVIEW, S.VEHICLE_ELIGIBILITY}),
    S.SIMULATION: frozenset(
        {S.DOCUMENT_COLLECTION, S.HUMAN_REVIEW, S.VEHICLE_ELIGIBILITY, S.PROFILING}
    ),
    S.DOCUMENT_COLLECTION: frozenset(
        {
            S.DOCUMENT_VALIDATION,
            S.HUMAN_REVIEW,
            S.VEHICLE_ELIGIBILITY,
            S.PROFILING,
            S.SIMULATION,
        }
    ),
    S.DOCUMENT_VALIDATION: frozenset(
        {
            S.READY_FOR_FINANCIAL,
            S.NEEDS_CORRECTION,
            S.HUMAN_REVIEW,
            S.DOCUMENT_COLLECTION,
            S.VEHICLE_ELIGIBILITY,
            S.PROFILING,
            S.SIMULATION,
        }
    ),
    S.NEEDS_CORRECTION: frozenset(
        {
            S.DOCUMENT_COLLECTION,
            S.HUMAN_REVIEW,
            S.VEHICLE_ELIGIBILITY,
            S.PROFILING,
            S.SIMULATION,
        }
    ),
    # Desde revisión humana nunca se va directo a READY (TDD §6.8).
    S.HUMAN_REVIEW: frozenset(
        {
            S.NEEDS_CORRECTION,
            S.VEHICLE_ELIGIBILITY,
            S.PROFILING,
            S.SIMULATION,
            S.DOCUMENT_COLLECTION,
            S.DOCUMENT_VALIDATION,
        }
    ),
    S.READY_FOR_FINANCIAL: frozenset(),
    S.REJECTED: frozenset(),
}


class TransitionError(Exception):
    def __init__(self, source: WorkflowState, target: WorkflowState) -> None:
        super().__init__(f"{source} -> {target}")
        self.source = source
        self.target = target


def check_transition(source: WorkflowState, target: WorkflowState) -> None:
    if target not in ALLOWED[source]:
        raise TransitionError(source, target)
