"""Gate final «listo para financiera» (TDD §6.7). Función pura: no lee la base ni llama a nadie.

La tool `mark_ready_for_financial` carga la evidencia bajo el bloqueo del caso y la pasa aquí.
Cada condición que falla indica la etapa de recuperación determinística; nunca se escribe un
READY parcial.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.documents import SLOTS
from app.domain.eligibility import EligibilityStatus, evaluate_eligibility
from app.domain.simulation import schedule_consistent
from app.domain.states import MAIN_PATH, WorkflowState

S = WorkflowState

# Etapas de recuperación además de las del camino principal.
WAIT_CUSTOMER = "WAIT_CUSTOMER"  # hay una propuesta por confirmar: no se retrocede
WAIT_PROVIDER = "WAIT_PROVIDER"  # operación material abierta: se espera o reconcilia
REVALIDATE = "REVALIDATE"  # evidencia vigente pero validación obsoleta: se revalida
IN_REVIEW = "IN_REVIEW"  # revisión humana abierta
NOT_APPLICABLE = "NOT_APPLICABLE"  # etapa incorrecta: 409, sin transición


@dataclass(frozen=True)
class ProfileEvidence:
    id: str
    outcome: str
    expires_at: datetime
    policy_version: str
    request_fingerprint: str
    annual_nominal_rate: Decimal | None
    max_financed_principal: Decimal | None


@dataclass(frozen=True)
class QuoteEvidence:
    id: str
    amount: Decimal
    expires_at: datetime
    status: str
    request_fingerprint: str


@dataclass(frozen=True)
class OfferEvidence:
    id: str
    status: str
    expires_at: datetime
    profile_id: str
    quote_id: str | None
    cash_amount: Decimal
    key_cost: Decimal
    financed_principal: Decimal
    annual_nominal_rate: Decimal
    term_months: int
    regular_payment: Decimal
    last_payment: Decimal
    total_payment: Decimal
    schedule: tuple[dict[str, Any], ...]
    display_hash: str
    computed_display_hash: str
    policy_version: str


@dataclass(frozen=True)
class SelectionEvidence:
    offer_id: str
    displayed_offer_hash: str
    selected_at: datetime


@dataclass(frozen=True)
class DocumentEvidence:
    id: str
    slot: str
    kind: str
    sha256: str
    extraction_id: str | None
    extraction_sha256: str | None
    extraction_hash: str | None


@dataclass(frozen=True)
class Evidence:
    state: str
    input_revision: int
    policy_version: str
    profile_complete: bool
    pending_proposal: bool
    owned_by_customer: bool | None
    blocking_debt: bool | None
    has_second_key: bool | None
    eligibility: dict[str, Any] | None
    bureau_fingerprint: str | None
    vehicle_fingerprint: str | None
    payment_capacity: Decimal | None
    profile: ProfileEvidence | None
    quote: QuoteEvidence | None
    offer: OfferEvidence | None
    selection: SelectionEvidence | None
    last_invalidation_at: datetime | None
    documents: tuple[DocumentEvidence, ...]
    rules_now: tuple[tuple[str, str], ...]  # (rule_id, status) recalculadas contra `now`
    validation_fingerprint: str | None  # huella de las validaciones activas guardadas
    current_validation_fingerprint: str  # huella recalculada con la evidencia vigente
    open_review: bool
    open_material_operations: int
    advisor_request_open: bool


@dataclass(frozen=True)
class Check:
    code: str
    ok: bool
    recovery: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Verdict:
    checks: tuple[Check, ...]
    fingerprint: str
    evaluated_at: datetime
    failed: tuple[Check, ...] = field(default=())

    @property
    def all_pass(self) -> bool:
        return not self.failed

    @property
    def recovery(self) -> str | None:
        """La etapa más temprana entre las condiciones que fallan (§6.7)."""
        if not self.failed:
            return None
        stages = [c.recovery for c in self.failed]
        for special in (NOT_APPLICABLE, IN_REVIEW, WAIT_PROVIDER, WAIT_CUSTOMER):
            if special in stages:
                return special
        main = [s for s in stages if s in {m.value for m in MAIN_PATH}]
        if main:
            return min(main, key=lambda s: [m.value for m in MAIN_PATH].index(s))
        return REVALIDATE

    def safe_evidence(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "evaluated_at": self.evaluated_at.isoformat(),
            "checks": [c.code for c in self.checks],
            "failed": [{"code": c.code, "recovery": c.recovery} for c in self.failed],
        }


def _offer_consistent(offer: OfferEvidence) -> bool:
    """La oferta del cotizador cuadra y no se alteró: llave una vez y calendario coherente.

    No recalcula cuotas (las decide el cotizador, decisión 0038): verifica su coherencia.
    """
    return (
        offer.financed_principal == offer.cash_amount + offer.key_cost
        and schedule_consistent(
            offer.schedule,
            principal=offer.financed_principal,
            term_months=offer.term_months,
            regular_payment=offer.regular_payment,
            last_payment=offer.last_payment,
            total_payment=offer.total_payment,
        )  # fmt: skip
        and offer.display_hash == offer.computed_display_hash
    )


def gate_fingerprint(ev: Evidence) -> str:
    material = {
        "input_revision": ev.input_revision,
        "policy_version": ev.policy_version,
        "profile": ev.profile.id if ev.profile else None,
        "quote": ev.quote.id if ev.quote else None,
        "offer": ev.offer.id if ev.offer else None,
        "offer_hash": ev.offer.display_hash if ev.offer else None,
        "selection": ev.selection.offer_id if ev.selection else None,
        "documents": sorted((d.slot, d.sha256, d.extraction_hash or "") for d in ev.documents),
        "validation": ev.current_validation_fingerprint,
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def readiness(ev: Evidence, now: datetime) -> Verdict:
    checks: list[Check] = []

    def check(code: str, ok: bool, recovery: str, detail: str | None = None) -> None:
        checks.append(Check(code, ok, None if ok else recovery, None if ok else detail))

    # 1. Etapa, datos completos y sin propuestas pendientes.
    check("STATE", ev.state == S.DOCUMENT_VALIDATION.value, NOT_APPLICABLE, ev.state)
    check("PROFILE_COMPLETE", ev.profile_complete, S.PROFILING.value)
    check("NO_PENDING_PROPOSAL", not ev.pending_proposal, WAIT_CUSTOMER)

    # 2. Elegibilidad sobre la revisión actual de los datos.
    # Se recalcula con las respuestas vigentes y se compara con el dictamen guardado: si las
    # respuestas cambiaron sin reevaluar, el dictamen guardado ya no vale.
    elig = ev.eligibility or {}
    now_result = evaluate_eligibility(ev.owned_by_customer, ev.blocking_debt, ev.has_second_key)
    check(
        "ELIGIBILITY",
        ev.owned_by_customer is True
        and ev.blocking_debt is False
        and now_result.status is EligibilityStatus.ELIGIBLE
        and elig.get("status") == EligibilityStatus.ELIGIBLE.value
        and elig.get("key_quote_required") == now_result.key_quote_required,
        S.VEHICLE_ELIGIBILITY.value,
    )

    # 3. Perfil crediticio vigente y coherente con la política del caso.
    p = ev.profile
    check(
        "CREDIT_PROFILE",
        p is not None
        and p.outcome == "OK"
        and p.expires_at > now
        and p.policy_version == ev.policy_version
        and p.request_fingerprint == ev.bureau_fingerprint,
        S.PROFILING.value,
    )

    # 4. Segunda llave: cotización vigente del vehículo actual y costo idéntico en la oferta.
    o = ev.offer
    if ev.has_second_key is False:
        qt = ev.quote
        key_ok = (
            qt is not None
            and qt.status == "VALID"
            and qt.expires_at > now
            and qt.request_fingerprint == ev.vehicle_fingerprint
            and o is not None
            and o.quote_id == qt.id
            and o.key_cost == qt.amount
        )
    else:
        key_ok = o is not None and o.key_cost == Decimal("0.00") and o.quote_id is None
    check("KEY_QUOTE", key_ok, S.PROFILING.value)

    # 5. Oferta actual del cotizador, no vencida, coherente y dentro de la capacidad de pago.
    offer_ok = (
        o is not None
        and o.status == "ACTIVE"
        and o.expires_at > now
        and p is not None
        and o.profile_id == p.id
        and o.policy_version == ev.policy_version
        and o.annual_nominal_rate == p.annual_nominal_rate
        and (p.max_financed_principal is None or o.financed_principal <= p.max_financed_principal)
        and ev.payment_capacity is not None
        and max(o.regular_payment, o.last_payment) <= ev.payment_capacity
        and _offer_consistent(o)
    )
    check("OFFER", offer_ok, S.SIMULATION.value)

    # 6. Selección explícita, activa, del hash mostrado y posterior a la última invalidación.
    sel = ev.selection
    check(
        "SELECTION",
        sel is not None
        and o is not None
        and sel.offer_id == o.id
        and sel.displayed_offer_hash == o.display_hash
        and (ev.last_invalidation_at is None or sel.selected_at >= ev.last_invalidation_at),
        S.SIMULATION.value,
    )

    # 7. Documentos activos, extracciones de sus propios bytes, reglas en PASS ahora mismo.
    slots = {d.slot for d in ev.documents}
    docs_ok = set(SLOTS) <= slots and all(
        d.extraction_id is not None and d.extraction_sha256 == d.sha256 for d in ev.documents
    )
    check("DOCUMENTS", docs_ok, S.DOCUMENT_COLLECTION.value)
    rules_ok = bool(ev.rules_now) and all(status == "PASS" for _, status in ev.rules_now)
    failed_rules = ",".join(r for r, st in ev.rules_now if st != "PASS")
    check("DOCUMENT_RULES", rules_ok, REVALIDATE, failed_rules or None)
    check(
        "VALIDATION_FINGERPRINT",
        ev.validation_fingerprint == ev.current_validation_fingerprint,
        REVALIDATE,
    )

    # 8. Sin revisión abierta, sin operación material incierta ni corrección pendiente.
    check("NO_OPEN_REVIEW", not ev.open_review, IN_REVIEW)
    check("NO_OPEN_OPERATIONS", ev.open_material_operations == 0, WAIT_PROVIDER)
    check("NO_OPEN_CORRECTION", not ev.advisor_request_open, WAIT_CUSTOMER)

    failed = tuple(c for c in checks if not c.ok)
    return Verdict(tuple(checks), gate_fingerprint(ev), now, failed)
