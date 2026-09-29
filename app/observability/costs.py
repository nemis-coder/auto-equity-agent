"""Estimación explícita, no factura: preserva desconocidos y separa caché del input normal."""

from app.observability.settings import ModelPrice


def estimate_cost(provider: str, model: str, usage: dict[str, int] | None,
                  prices: dict[str, ModelPrice]) -> tuple[float | None, str]:
    if usage is None:
        return None, "usage_unknown"
    if provider in {"fake", "scripted"}:
        return 0.0, "simulated"
    price = prices.get(f"{provider}:{model}")
    if price is None:
        return None, "rate_missing"
    cached = usage.get("cache_read", 0) + usage.get("cache_creation", 0)
    normal = usage.get("input", 0) - cached
    if normal < 0 or any(v < 0 for v in usage.values()):
        return None, "usage_invalid"
    total = normal * price.input + usage.get("output", 0) * price.output
    for kind in ("cache_read", "cache_creation"):
        tokens, rate = usage.get(kind, 0), getattr(price, kind)
        if tokens and rate is None:
            return None, "cache_rate_missing"
        total += tokens * (rate or 0)
    return round(total / 1_000_000, 10), "estimated"


def token_usage(raw: dict) -> dict[str, int]:
    """LangChain incluye la caché en input_tokens; los detalles son subconjuntos."""
    usage = {"input": int(raw.get("input_tokens") or 0),
             "output": int(raw.get("output_tokens") or 0)}
    details = raw.get("input_token_details") or {}
    for key in ("cache_read", "cache_creation"):
        if details.get(key):
            usage[key] = int(details[key])
    return usage
