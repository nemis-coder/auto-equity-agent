"""Declaraciones del cliente por grupo, con payload parcial y diff campo a campo (TDD §6.2).

- `vehicle` se persiste en `vehicles`; `profile`, en `cases.declared_profile`.
- Un campo omitido conserva su valor confirmado; `null` explícito significa "desconocido".
- El diff (tras normalizar) decide las invalidaciones; un valor repetido no invalida nada.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.states import WorkflowState

CENT = Decimal("0.01")
PERIOD_FACTORS = {
    "WEEKLY": Decimal(52) / Decimal(12),
    "BIWEEKLY_14D": Decimal(26) / Decimal(12),
    "SEMIMONTHLY": Decimal(2),
    "MONTHLY": Decimal(1),
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def normalize_vehicle_ref(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Address(_Strict):
    street: str = Field(min_length=1, max_length=120)
    external_number: str = Field(min_length=1, max_length=20)
    internal_number: str | None = Field(default=None, max_length=20)
    neighborhood: str = Field(min_length=1, max_length=120)
    municipality: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=60)
    postal_code: str = Field(pattern=r"^\d{5}$")
    country: Literal["MX"] = "MX"

    @model_validator(mode="after")
    def _separate_number(self) -> Address:
        if self.street.split()[-1] == self.external_number or self.street[-1:].isdigit():
            raise ValueError("La calle debe ir sin número; el número exterior va aparte.")
        return self


class DeclaredIncome(_Strict):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: Literal["MXN"]
    period: Literal["WEEKLY", "BIWEEKLY_14D", "SEMIMONTHLY", "MONTHLY"]
    basis: Literal["NET"]


class VehicleDeclaration(_Strict):
    owned_by_customer: bool | None = None
    blocking_debt: bool | None = None
    has_second_key: bool | None = None
    vehicle_ref: str | None = Field(default=None, min_length=3, max_length=40)
    make: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, min_length=1, max_length=60)
    year: int | None = Field(default=None, ge=1980, le=date.today().year + 1)
    declared_owner_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("vehicle_ref")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        ref = normalize_vehicle_ref(v)
        if not re.fullmatch(r"[A-Z0-9]{3,40}", ref):
            raise ValueError("vehicle_ref solo admite letras y números")
        return ref


class ProfileDeclaration(_Strict):
    full_name: str | None = Field(default=None, min_length=3, max_length=200)
    address: Address | None = None
    employment: Literal["SALARIED", "SELF_EMPLOYED"] | None = None
    employer_or_activity: str | None = Field(default=None, min_length=2, max_length=200)
    income: DeclaredIncome | None = None
    # Sin monto solicitado: las opciones (montos y plazos) las decide el cotizador (0038).

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, value: str | None) -> str | None:
        if value is not None and len(value.split()) < 2:
            raise ValueError("El nombre completo debe incluir nombre y apellido.")
        return value


VEHICLE_FIELDS = tuple(VehicleDeclaration.model_fields)
PROFILE_FIELDS = tuple(ProfileDeclaration.model_fields)
VEHICLE_ELIGIBILITY_FIELDS = ("owned_by_customer", "blocking_debt", "has_second_key")
GROUP_MODELS: dict[str, type[_Strict]] = {
    "vehicle": VehicleDeclaration,
    "profile": ProfileDeclaration,
}


def _canonical(value: Any) -> Any:
    """Forma normalizada y serializable para comparar y persistir."""
    if isinstance(value, BaseModel):
        return {k: _canonical(v) for k, v in value.model_dump().items()}
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return str(value.quantize(CENT, rounding=ROUND_HALF_UP))
    if isinstance(value, str):
        return _clean_text(value)
    return value


def parse_partial(group: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Valida el payload del grupo y devuelve solo los campos presentes, normalizados."""
    model = GROUP_MODELS[group].model_validate(payload)
    return {name: _canonical(getattr(model, name)) for name in model.model_fields_set}


def diff_fields(current: dict[str, Any], proposed: dict[str, Any]) -> set[str]:
    return {name for name, value in proposed.items() if current.get(name) != value}


def payload_hash(group: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"group": group, "fields": fields}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# Ampliación documentada en docs/auto_equity_TDD.md §6.2: make, model, year y declared_owner_name
# también identifican el vehículo y la cotización de llave, así que invalidan igual.
_RULES: tuple[tuple[frozenset[str], frozenset[str], WorkflowState], ...] = (
    (
        frozenset(VEHICLE_FIELDS),
        frozenset({"eligibility", "key_quote", "offers", "selection", "validation"}),
        WorkflowState.VEHICLE_ELIGIBILITY,
    ),
    (
        frozenset({"full_name", "address", "employment", "employer_or_activity", "income"}),
        frozenset(
            {"bureau_if_fingerprint_changed", "profile", "offers", "selection", "validation"}
        ),
        WorkflowState.PROFILING,
    ),
)
_ORDER = (WorkflowState.VEHICLE_ELIGIBILITY, WorkflowState.PROFILING, WorkflowState.SIMULATION)


def invalidations_for(diff: set[str]) -> tuple[frozenset[str], WorkflowState | None]:
    hits = [(deps, stage) for fields, deps, stage in _RULES if fields & diff]
    if not hits:
        return frozenset(), None
    deps = frozenset().union(*(d for d, _ in hits))
    return deps, min((s for _, s in hits), key=_ORDER.index)


def monthly_income(income: dict[str, Any]) -> Decimal:
    return Decimal(income["amount"]) * PERIOD_FACTORS[income["period"]]


def bureau_fingerprint(profile: dict[str, Any]) -> str:
    """Campos exactos que identifican una consulta a Buró (TDD §6.2)."""
    income = profile["income"]
    material = {
        "full_name": _clean_text(profile["full_name"]).casefold(),
        "address": profile["address"],
        "employment": profile["employment"],
        "monthly_income": str(monthly_income(income).quantize(CENT, rounding=ROUND_HALF_UP)),
        "currency": income["currency"],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def missing_profile_fields(profile: dict[str, Any]) -> list[str]:
    return [name for name in PROFILE_FIELDS if profile.get(name) is None]
