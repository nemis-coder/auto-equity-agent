"""Política documental `document_policy_v2` (TDD §6.6, supuesto S5).

Reglas puras sobre extracciones y declaraciones confirmadas. Cada regla devuelve PASS, FAIL o
UNKNOWN; UNKNOWN bloquea igual que FAIL. No se promedian resultados ni confianzas.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.declarations import PERIOD_FACTORS, monthly_income, normalize_vehicle_ref

POLICY_VERSION = "document_policy_v2"
MIN_CONFIDENCE = Decimal("0.90")
INCOME_TOLERANCE = Decimal("0.10")
MAX_INCOME_DOC_AGE_DAYS = 90

INCOME_KINDS = ("PAYSLIP", "INCOME_STATEMENT")
DOCUMENT_KINDS = ("IDENTITY", *INCOME_KINDS, "VEHICLE_OWNERSHIP")
SLOTS = ("IDENTITY", "INCOME", "VEHICLE_OWNERSHIP")
ADDRESS_PARTS = (
    "street",
    "external_number",
    "internal_number",
    "neighborhood",
    "municipality",
    "state",
    "postal_code",
)
# Campos que usan las reglas; su calidad se exige campo por campo.
CRITICAL_FIELDS = {
    "IDENTITY": ("full_name", *(f"address_{p}" for p in ADDRESS_PARTS), "issue_date",
                 "expiry_date"),
    "PAYSLIP": ("full_name", "employer_name", "income_amount", "currency", "period",
                "income_basis", "period_start", "period_end", "issue_date"),
    "INCOME_STATEMENT": ("full_name", "activity", "income_amount", "currency", "period",
                         "income_basis", "period_start", "period_end", "issue_date"),
    "VEHICLE_OWNERSHIP": ("owner_name", "vehicle_ref", "issue_date"),
}  # fmt: skip
NULLABLE_FIELDS = frozenset({"address_internal_number"})
EXPECTED_INCOME_KIND = {"SALARIED": "PAYSLIP", "SELF_EMPLOYED": "INCOME_STATEMENT"}


def slot_of(kind: str) -> str:
    return "INCOME" if kind in INCOME_KINDS else kind


class RuleStatus(enum.StrEnum):
    PASS = "PASS"  # noqa: S105 - estado de regla, no una contraseña
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: RuleStatus
    reason_code: str
    refs: tuple[dict[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ActiveDocument:
    document_id: str
    declared_kind: str
    extraction: dict[str, Any] | None


# --- Normalización -----------------------------------------------------------------------

_DICTIONARY = {"av": "avenida", "cdmx": "ciudad de mexico"}


def normalize_text(value: str) -> str:
    """NFKD, sin marcas diacríticas, casefold, puntuación a espacios, espacios colapsados."""
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w\s]|_", " ", plain).split())


def normalize_address_part(value: str) -> str:
    return " ".join(_DICTIONARY.get(t, t) for t in normalize_text(value).split())


def _value(extraction: dict[str, Any], name: str) -> Any:
    spec = (extraction.get("fields") or {}).get(name) or {}
    return spec.get("value")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _result(rule: str, status: RuleStatus, reason: str, *refs: dict[str, str]) -> RuleResult:
    return RuleResult(rule, status, reason, tuple(refs))


def _ref(slot: str, doc: ActiveDocument | None, name: str | None = None) -> dict[str, str]:
    ref = {"slot": slot}
    if doc is not None:
        ref["document_id"] = doc.document_id
    if name:
        ref["field"] = name
    return ref


# --- Reglas --------------------------------------------------------------------------------


def rule_doc_required(docs: dict[str, ActiveDocument]) -> RuleResult:
    for slot in SLOTS:
        doc = docs.get(slot)
        if doc is None:
            return _result("DOC_REQUIRED", RuleStatus.FAIL, "DOCUMENT_MISSING", _ref(slot, None))
        if doc.extraction is None:
            return _result(
                "DOC_REQUIRED", RuleStatus.UNKNOWN, "EXTRACTION_PENDING", _ref(slot, doc)
            )
        if doc.extraction.get("detected_type") != doc.declared_kind:
            return _result(
                "DOC_REQUIRED", RuleStatus.FAIL, "DOCUMENT_TYPE_MISMATCH", _ref(slot, doc)
            )
    return _result("DOC_REQUIRED", RuleStatus.PASS, "OK")


def rule_extraction_quality(docs: dict[str, ActiveDocument]) -> RuleResult:
    unknown: RuleResult | None = None
    for slot, doc in docs.items():
        if doc.extraction is None:
            unknown = unknown or _result(
                "EXTRACTION_QUALITY", RuleStatus.UNKNOWN, "EXTRACTION_PENDING", _ref(slot, doc)
            )
            continue
        if not doc.extraction.get("legible"):
            return _result("EXTRACTION_QUALITY", RuleStatus.FAIL, "ILLEGIBLE", _ref(slot, doc))
        fields = doc.extraction.get("fields") or {}
        for name in CRITICAL_FIELDS.get(doc.declared_kind, ()):
            spec = fields.get(name) or {}
            value = spec.get("value")
            if value is None and name not in NULLABLE_FIELDS:
                return _result(
                    "EXTRACTION_QUALITY", RuleStatus.FAIL, "FIELD_MISSING", _ref(slot, doc, name)
                )
            if value is not None and not spec.get("evidence_text"):
                return _result(
                    "EXTRACTION_QUALITY", RuleStatus.FAIL, "EVIDENCE_MISSING",
                    _ref(slot, doc, name),
                )  # fmt: skip
            if spec.get("human_verified") is True:
                continue  # solo lo produce AMEND_EXTRACTION del asesor asignado (TDD §6.8)
            confidence = _decimal(spec.get("confidence"))
            if confidence is None:
                unknown = unknown or _result(
                    "EXTRACTION_QUALITY", RuleStatus.UNKNOWN, "CONFIDENCE_MISSING",
                    _ref(slot, doc, name),
                )  # fmt: skip
            elif confidence < MIN_CONFIDENCE:
                return _result(
                    "EXTRACTION_QUALITY", RuleStatus.FAIL, "LOW_CONFIDENCE", _ref(slot, doc, name)
                )
    return unknown or _result("EXTRACTION_QUALITY", RuleStatus.PASS, "OK")


def _income(docs: dict[str, ActiveDocument]) -> tuple[ActiveDocument | None, dict[str, Any]]:
    doc = docs.get("INCOME")
    return doc, (doc.extraction if doc and doc.extraction else {})


# Etiquetas literales, no intervalos calculados ni palabras sueltas de una instrucción.
# Lo no reconocido queda UNKNOWN: el asesor puede verificar el original, no forzar un PASS.
_INCOME_LABELS = {
    "period": {
        "WEEKLY": r"semanal|weekly|cada (?:7|siete) dias",
        "BIWEEKLY_14D": r"catorcenal|biweekly 14d|cada (?:14|catorce) dias|every 14 days",
        "SEMIMONTHLY": r"quincenal|(?:primera |segunda )?quincena|semimonthly|dos veces al mes",
        "MONTHLY": r"mensual|monthly|cada mes",
    },
    "income_basis": {
        "NET": r"neto|net|liquido|despues de impuestos|after tax",
        "GROSS": r"bruto|gross|antes de impuestos|before tax",
    },
}
_LABEL_PREFIX = {
    "period": r"(?:(?:periodicidad|frecuencia(?: de pago)?|pay frequency|nomina|pago) )?",
    "income_basis": r"(?:(?:base(?: del ingreso)?|ingreso|salario|sueldo|importe|total) )?",
}


def _income_evidence_supports(ext: dict[str, Any], field_name: str, value: str) -> bool:
    spec = (ext.get("fields") or {}).get(field_name) or {}
    evidence = normalize_text(str(spec.get("evidence_text") or ""))
    if not evidence:
        return False
    if spec.get("human_verified") is True:
        return True  # solo el asesor autenticado puede producirlo; las reglas siguen aplicando
    labels = _INCOME_LABELS[field_name]
    found = {name for name, pattern in labels.items() if re.search(rf"\b(?:{pattern})\b", evidence)}
    return found == {value} and re.match(
        rf"^{_LABEL_PREFIX[field_name]}(?:{labels[value]})\b", evidence
    ) is not None


def rule_income_basis(docs, profile) -> RuleResult:
    doc, ext = _income(docs)
    basis = _value(ext, "income_basis")
    ref = _ref("INCOME", doc, "income_basis")
    if basis is None:
        return _result("INCOME_BASIS", RuleStatus.UNKNOWN, "INCOME_BASIS_UNKNOWN", ref)
    if basis != "NET" or profile["income"]["basis"] != "NET":
        return _result("INCOME_BASIS", RuleStatus.FAIL, "INCOME_NOT_NET", ref)
    if not _income_evidence_supports(ext, "income_basis", basis):
        return _result(
            "INCOME_BASIS", RuleStatus.UNKNOWN, "INCOME_BASIS_UNSUPPORTED_EVIDENCE", ref
        )
    return _result("INCOME_BASIS", RuleStatus.PASS, "OK", ref)


def rule_income_currency(docs, profile) -> RuleResult:
    doc, ext = _income(docs)
    currency = _value(ext, "currency")
    ref = _ref("INCOME", doc, "currency")
    if currency is None:
        return _result("INCOME_CURRENCY", RuleStatus.UNKNOWN, "INCOME_CURRENCY_UNKNOWN", ref)
    if currency != "MXN" or profile["income"]["currency"] != "MXN":
        return _result("INCOME_CURRENCY", RuleStatus.FAIL, "INCOME_CURRENCY_MISMATCH", ref)
    return _result("INCOME_CURRENCY", RuleStatus.PASS, "OK", ref)


def rule_income_period(docs, profile) -> RuleResult:
    doc, ext = _income(docs)
    period = _value(ext, "period")
    ref = _ref("INCOME", doc, "period")
    if period not in PERIOD_FACTORS or profile["income"]["period"] not in PERIOD_FACTORS:
        return _result("INCOME_PERIOD", RuleStatus.UNKNOWN, "INCOME_PERIOD_UNKNOWN", ref)
    if not _income_evidence_supports(ext, "period", period):
        return _result(
            "INCOME_PERIOD", RuleStatus.UNKNOWN, "INCOME_PERIOD_UNSUPPORTED_EVIDENCE", ref
        )
    return _result("INCOME_PERIOD", RuleStatus.PASS, "OK", ref)


def rule_income_match(docs, profile, prerequisites: list[RuleResult]) -> RuleResult:
    doc, ext = _income(docs)
    ref = _ref("INCOME", doc, "income_amount")
    if any(r.status is not RuleStatus.PASS for r in prerequisites):
        return _result("INCOME_MATCH", RuleStatus.UNKNOWN, "INCOME_NOT_COMPARABLE", ref)
    amount = _decimal(_value(ext, "income_amount"))
    declared = monthly_income(profile["income"])
    if amount is None or amount <= 0 or declared <= 0:
        return _result("INCOME_MATCH", RuleStatus.UNKNOWN, "INCOME_AMOUNT_UNKNOWN", ref)
    extracted = amount * PERIOD_FACTORS[_value(ext, "period")]
    # Comparación antes de redondear para mostrar; inclusiva en el 10 %.
    if abs(extracted - declared) / declared <= INCOME_TOLERANCE:
        return _result("INCOME_MATCH", RuleStatus.PASS, "OK", ref)
    return _result("INCOME_MATCH", RuleStatus.FAIL, "INCOME_MISMATCH", ref)


def _names_equal(a: Any, b: Any) -> bool:
    return isinstance(a, str) and isinstance(b, str) and normalize_text(a) == normalize_text(b)


def rule_name_match(docs, profile) -> RuleResult:
    doc = docs.get("IDENTITY")
    ref = _ref("IDENTITY", doc, "full_name")
    name = _value(doc.extraction, "full_name") if doc and doc.extraction else None
    if name is None:
        return _result("NAME_MATCH", RuleStatus.UNKNOWN, "NAME_UNKNOWN", ref)
    if _names_equal(name, profile["full_name"]):
        return _result("NAME_MATCH", RuleStatus.PASS, "OK", ref)
    return _result("NAME_MATCH", RuleStatus.FAIL, "NAME_MISMATCH", ref)


def rule_address_match(docs, profile) -> RuleResult:
    doc = docs.get("IDENTITY")
    ext = doc.extraction if doc and doc.extraction else {}
    declared = profile["address"]
    for part in ADDRESS_PARTS:
        name = f"address_{part}"
        extracted = _value(ext, name)
        expected = declared.get(part)
        ref = _ref("IDENTITY", doc, name)
        if name in NULLABLE_FIELDS and (expected is None or extracted is None):
            # Interior `null` solo coincide con `null`: la ausencia es un valor leído.
            if expected is None and extracted is None:
                continue
            return _result("ADDRESS_MATCH", RuleStatus.FAIL, "ADDRESS_MISMATCH", ref)
        if extracted is None:
            return _result("ADDRESS_MATCH", RuleStatus.UNKNOWN, "ADDRESS_FIELD_UNKNOWN", ref)
        if part in ("external_number", "internal_number", "postal_code"):
            equal = str(extracted).strip().upper() == str(expected).strip().upper()
        else:
            equal = normalize_address_part(str(extracted)) == normalize_address_part(expected)
        if not equal:
            return _result("ADDRESS_MATCH", RuleStatus.FAIL, "ADDRESS_MISMATCH", ref)
    return _result("ADDRESS_MATCH", RuleStatus.PASS, "OK", _ref("IDENTITY", doc))


def rule_employment_match(docs, profile) -> RuleResult:
    doc, ext = _income(docs)
    expected_kind = EXPECTED_INCOME_KIND[profile["employment"]]
    if doc is None or doc.declared_kind != expected_kind:
        return _result(
            "EMPLOYMENT_MATCH", RuleStatus.FAIL, "INCOME_DOCUMENT_TYPE_MISMATCH",
            _ref("INCOME", doc),
        )  # fmt: skip
    if not _names_equal(_value(ext, "full_name"), profile["full_name"]):
        return _result(
            "EMPLOYMENT_MATCH", RuleStatus.FAIL, "INCOME_DOCUMENT_NAME_MISMATCH",
            _ref("INCOME", doc, "full_name"),
        )  # fmt: skip
    source = "employer_name" if expected_kind == "PAYSLIP" else "activity"
    if not _names_equal(_value(ext, source), profile["employer_or_activity"]):
        return _result(
            "EMPLOYMENT_MATCH", RuleStatus.FAIL, "EMPLOYER_MISMATCH", _ref("INCOME", doc, source)
        )
    return _result("EMPLOYMENT_MATCH", RuleStatus.PASS, "OK", _ref("INCOME", doc))


def rule_dates(docs, today: date) -> RuleResult:
    checks: list[tuple[str, str, bool | None, str]] = []  # slot, campo, ok, motivo
    for slot, doc in docs.items():
        ext = doc.extraction or {}
        issued = _parse_date(_value(ext, "issue_date"))
        checks.append((slot, "issue_date", None if issued is None else issued <= today,
                       "DATE_IN_FUTURE"))  # fmt: skip
        if doc.declared_kind == "IDENTITY":
            expiry = _parse_date(_value(ext, "expiry_date"))
            checks.append((slot, "expiry_date", None if expiry is None else expiry >= today,
                           "IDENTITY_EXPIRED"))  # fmt: skip
        if doc.declared_kind in INCOME_KINDS:
            start = _parse_date(_value(ext, "period_start"))
            end = _parse_date(_value(ext, "period_end"))
            if start is None or end is None:
                checks.append((slot, "period_end", None, "DATE_MISSING"))
            else:
                checks.append((slot, "period_start", start <= end, "PERIOD_INVALID"))
                checks.append((slot, "period_end", end <= today, "DATE_IN_FUTURE"))
                checks.append((slot, "period_end",
                               (today - end).days <= MAX_INCOME_DOC_AGE_DAYS,
                               "INCOME_DOCUMENT_TOO_OLD"))  # fmt: skip
    for slot, name, ok, reason in checks:
        if ok is False:
            return _result("DATES", RuleStatus.FAIL, reason, _ref(slot, docs[slot], name))
    for slot, name, ok, _ in checks:
        if ok is None:
            return _result(
                "DATES", RuleStatus.UNKNOWN, "DATE_MISSING", _ref(slot, docs[slot], name)
            )
    return _result("DATES", RuleStatus.PASS, "OK")


def rule_vehicle_match(docs, profile, vehicle: dict[str, Any]) -> RuleResult:
    doc = docs.get("VEHICLE_OWNERSHIP")
    ext = doc.extraction if doc and doc.extraction else {}
    ref_value = _value(ext, "vehicle_ref")
    owner = _value(ext, "owner_name")
    if ref_value is None or owner is None or vehicle.get("vehicle_ref") is None:
        return _result(
            "VEHICLE_MATCH", RuleStatus.UNKNOWN, "VEHICLE_DATA_UNKNOWN",
            _ref("VEHICLE_OWNERSHIP", doc),
        )  # fmt: skip
    if normalize_vehicle_ref(str(ref_value)) != normalize_vehicle_ref(vehicle["vehicle_ref"]):
        return _result(
            "VEHICLE_MATCH", RuleStatus.FAIL, "VEHICLE_REF_MISMATCH",
            _ref("VEHICLE_OWNERSHIP", doc, "vehicle_ref"),
        )  # fmt: skip
    if not _names_equal(owner, profile["full_name"]):
        return _result(
            "VEHICLE_MATCH", RuleStatus.FAIL, "VEHICLE_OWNER_MISMATCH",
            _ref("VEHICLE_OWNERSHIP", doc, "owner_name"),
        )  # fmt: skip
    return _result("VEHICLE_MATCH", RuleStatus.PASS, "OK", _ref("VEHICLE_OWNERSHIP", doc))


def evaluate(
    docs: dict[str, ActiveDocument],
    profile: dict[str, Any],
    vehicle: dict[str, Any],
    now: datetime,
) -> list[RuleResult]:
    """Ejecuta todas las reglas. Si falta un documento, las demás reglas no se evalúan."""
    required = rule_doc_required(docs)
    if required.status is not RuleStatus.PASS:
        return [required]
    basis = rule_income_basis(docs, profile)
    currency = rule_income_currency(docs, profile)
    period = rule_income_period(docs, profile)
    return [
        required,
        rule_extraction_quality(docs),
        basis,
        currency,
        period,
        rule_income_match(docs, profile, [basis, currency, period]),
        rule_name_match(docs, profile),
        rule_address_match(docs, profile),
        rule_employment_match(docs, profile),
        rule_dates(docs, now.date()),
        rule_vehicle_match(docs, profile, vehicle),
    ]


def all_pass(results: list[RuleResult]) -> bool:
    return bool(results) and all(r.status is RuleStatus.PASS for r in results)
