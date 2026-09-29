"""Política crediticia sintética versionada (TDD §6.4, supuesto S1).

Deriva el perfil a partir de la respuesta del Buró. No usa IA: el score lo entrega el
proveedor (mock) y las reglas están en `config/<policy_version>.json`.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@dataclass(frozen=True)
class ProfileBand:
    name: str
    min_score: int
    max_score: int
    annual_nominal_rate: Decimal
    max_financed_principal: Decimal


@dataclass(frozen=True)
class Policy:
    version: str
    currency: str
    score_range: tuple[int, int]
    bands: tuple[ProfileBand, ...]
    review_below_score: int
    review_if_delinquencies_above: int
    max_payment_to_income_ratio: Decimal
    profile_validity_days: int


@lru_cache
def load_policy(version: str) -> Policy:
    path = CONFIG_DIR / f"{version}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw["policy_version"] != version:
        raise ValueError(f"{path} no declara la versión {version}")
    return Policy(
        version=version,
        currency=raw["currency"],
        score_range=tuple(raw["bureau_score_range"]),
        bands=tuple(
            ProfileBand(
                name=b["name"],
                min_score=b["min_score"],
                max_score=b["max_score"],
                annual_nominal_rate=Decimal(b["annual_nominal_rate"]),
                max_financed_principal=Decimal(b["max_financed_principal"]),
            )
            for b in raw["profiles"]
        ),
        review_below_score=raw["review_below_score"],
        review_if_delinquencies_above=raw["review_if_delinquencies_last_12m_above"],
        max_payment_to_income_ratio=Decimal(raw["max_payment_to_income_ratio"]),
        profile_validity_days=raw["profile_validity_days"],
    )


class ProfileOutcome(enum.StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"


class MalformedBureauResponse(ValueError):
    """Respuesta inválida del proveedor: es un fallo técnico, no un perfil de mayor riesgo."""


@dataclass(frozen=True)
class DerivedProfile:
    outcome: ProfileOutcome
    reason_code: str
    band: str | None
    annual_nominal_rate: Decimal | None
    max_financed_principal: Decimal | None


def _require_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedBureauResponse(f"{key} inválido")
    return value


def derive_profile(policy: Policy, response: dict[str, Any]) -> DerivedProfile:
    status = response.get("status")
    if status not in ("OK", "REVIEW"):
        raise MalformedBureauResponse("status inválido")
    score = _require_int(response, "score")
    low, high = policy.score_range
    if not low <= score <= high:
        raise MalformedBureauResponse("score fuera de rango")
    history = response.get("history_summary")
    if not isinstance(history, dict):
        raise MalformedBureauResponse("history_summary inválido")
    delinquencies = _require_int(history, "delinquencies_last_12m")
    if delinquencies < 0:
        raise MalformedBureauResponse("delinquencies negativo")
    conditions = response.get("conditions")
    if not isinstance(conditions, dict) or conditions.get("currency") != policy.currency:
        raise MalformedBureauResponse("conditions inválidas")
    try:
        provider_max = Decimal(str(conditions["max_financed_principal"]))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise MalformedBureauResponse("max_financed_principal inválido") from exc
    if provider_max <= 0:
        raise MalformedBureauResponse("max_financed_principal inválido")

    if status == "REVIEW":
        return DerivedProfile(ProfileOutcome.REVIEW, "BUREAU_STATUS_REVIEW", None, None, None)
    if delinquencies > policy.review_if_delinquencies_above:
        return DerivedProfile(ProfileOutcome.REVIEW, "BUREAU_DELINQUENCIES", None, None, None)
    if score < policy.review_below_score:
        return DerivedProfile(ProfileOutcome.REVIEW, "SCORE_BELOW_POLICY", None, None, None)
    band = next(b for b in policy.bands if b.min_score <= score <= b.max_score)
    return DerivedProfile(
        ProfileOutcome.OK,
        f"PROFILE_{band.name}",
        band.name,
        band.annual_nominal_rate,
        min(band.max_financed_principal, provider_max),
    )
