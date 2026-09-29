"""Reglas determinísticas de elegibilidad del auto (challenge §3.A; TDD §4.4).

- No titular → rechazo.
- Adeudo que impide la garantía → rechazo.
- Sin segunda llave → no es rechazo: se cotiza y se suma al plan.
- Un dato desconocido (`None`) nunca equivale a `False`: se pregunta.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class EligibilityStatus(enum.StrEnum):
    ELIGIBLE = "ELIGIBLE"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class RejectionReason(enum.StrEnum):
    VEHICLE_NOT_OWNED = "VEHICLE_NOT_OWNED"
    BLOCKING_DEBT = "BLOCKING_DEBT"


@dataclass(frozen=True)
class EligibilityResult:
    status: EligibilityStatus
    rejection_reasons: tuple[RejectionReason, ...] = ()
    missing: tuple[str, ...] = ()
    key_quote_required: bool = False
    rules_version: str = field(default="eligibility_v1")


def evaluate_eligibility(
    owned_by_customer: bool | None,
    blocking_debt: bool | None,
    has_second_key: bool | None,
) -> EligibilityResult:
    reasons: list[RejectionReason] = []
    if owned_by_customer is False:
        reasons.append(RejectionReason.VEHICLE_NOT_OWNED)
    if blocking_debt is True:
        reasons.append(RejectionReason.BLOCKING_DEBT)
    if reasons:
        # Un "no" confirmado basta para rechazar aunque falten otras respuestas.
        return EligibilityResult(EligibilityStatus.REJECTED, rejection_reasons=tuple(reasons))

    missing = tuple(
        name
        for name, value in (
            ("owned_by_customer", owned_by_customer),
            ("blocking_debt", blocking_debt),
            ("has_second_key", has_second_key),
        )
        if value is None
    )
    if missing:
        return EligibilityResult(EligibilityStatus.PENDING, missing=missing)
    return EligibilityResult(EligibilityStatus.ELIGIBLE, key_quote_required=has_second_key is False)
