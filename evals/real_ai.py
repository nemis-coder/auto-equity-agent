"""Pista de IA real (TDD §8.1, pista 2; métricas de §8.2).

Entrega al extractor real solo los bytes del documento (nunca las etiquetas), compara cada
campo crítico con la verdad etiquetada y ejecuta las 11 reglas deterministas con los datos
declarados del expediente. Reporta todas las repeticiones, sin elegir la mejor.

Sin un extractor real configurado (OpenAI o Anthropic), el informe queda
`NOT_RUN` con su motivo: esta pista nunca se ejecuta con el extractor de fixtures ni se
reporta como calidad real.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.config import Settings
from app.domain.documents import MIN_CONFIDENCE, ActiveDocument, all_pass, evaluate, slot_of
from app.providers.extraction import PROMPT_VERSION, ExtractionUnavailable
from app.tools.file_intake import inspect_upload

DATASET = Path(__file__).resolve().parent / "datasets" / "real_ai"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
THRESHOLDS = {"exactitud": 0.95, "abstencion": 1.0, "completitud": 0.90}


def load_labels() -> dict[str, Any]:
    return json.loads((DATASET / "labels.json").read_text(encoding="utf-8"))


def normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return str(Decimal(text.replace(",", "").replace("$", "").split()[0]).normalize())
    except (InvalidOperation, IndexError):
        pass
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def _abstained(spec: dict[str, Any]) -> bool:
    """No inventó: sin valor, o con una confianza que las reglas no aceptan."""
    if spec.get("human_verified"):
        return False
    confidence = spec.get("confidence")
    return spec.get("value") is None or confidence is None or confidence < float(MIN_CONFIDENCE)


def _not_run(reason: str, split: str, repeats: int) -> dict[str, Any]:
    return {
        "suite": "real_ai",
        "status": "NOT_RUN",
        "reason": reason,
        "split": split,
        "repeats": repeats,
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": [c["case_id"] for c in load_labels()["cases"] if c["split"] == split],
        "metrics": {},
    }


async def _run_case(case: dict, extractor, settings: Settings, repeat: int) -> dict[str, Any]:
    by_slot: dict[str, ActiveDocument] = {}
    fields_out, tokens, latencies, error = [], {"input": 0, "output": 0}, [], None
    for doc in case["documents"]:
        content = (DATASET / doc["file"]).read_bytes()
        info = inspect_upload(
            content,
            max_bytes=settings.max_file_bytes,
            max_pages=settings.max_document_pages,
            max_pixels=settings.max_image_pixels,
        )
        started = time.monotonic()
        try:
            result = await extractor.extract(content, info.mime_type, doc["kind"], info.page_count)
        except ExtractionUnavailable as exc:
            error = f"{doc['kind']}: {type(exc).__name__}"
            continue
        latencies.append((time.monotonic() - started) * 1000)
        tokens["input"] += result.input_tokens
        tokens["output"] += result.output_tokens
        extracted = result.extraction.get("fields", {})
        by_slot[slot_of(doc["kind"])] = ActiveDocument(
            doc["sha256"], doc["kind"], result.extraction
        )
        for name, truth in doc["truth"].items():
            spec = extracted.get(name) or {}
            row = {"document": doc["kind"], "field": name}
            if truth is None:
                row |= {"kind": "abstain", "ok": _abstained(spec)}
            else:
                row |= {"kind": "present", "ok": normalize(spec.get("value")) == normalize(truth)}
            fields_out.append(row)
    results = evaluate(
        by_slot, case["declared_profile"], {"vehicle_ref": case["declared_vehicle_ref"]}, NOW
    )
    failing = sorted(r.rule_id for r in results if r.status.value != "PASS")
    passed = all_pass(results) and error is None
    return {
        "case_id": case["case_id"],
        "repeat": repeat,
        "outcome": case["outcome"],
        "template": case["template"],
        "variant": case["variant"],
        "expected_failures": case["expected_failures"],
        "failing_rules": failing,
        "all_rules_pass": passed,
        "detected": set(case["expected_failures"]) <= set(failing),
        "fields": fields_out,
        "tokens": tokens,
        "latency_ms": latencies,
        "error": error,
    }


def _ratio(num: int, den: int) -> dict[str, Any]:
    return {"numerator": num, "denominator": den, "value": round(num / den, 4) if den else None}


def metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    present = [f for r in runs for f in r["fields"] if f["kind"] == "present"]
    abstain = [f for r in runs for f in r["fields"] if f["kind"] == "abstain"]
    negatives = [r for r in runs if r["outcome"] == "NEGATIVE"]
    valid = [r for r in runs if r["outcome"] == "VALID"]
    by_field: dict[str, list[bool]] = {}
    for f in present:
        by_field.setdefault(f"{f['document']}.{f['field']}", []).append(f["ok"])
    by_rule: dict[str, list[bool]] = {}
    for r in negatives:
        for rule in r["expected_failures"]:
            by_rule.setdefault(rule, []).append(rule in r["failing_rules"])
    lat = sorted(x for r in runs for x in r["latency_ms"])
    readies = [r for r in runs if r["all_rules_pass"]]
    out = {
        "exactitud_de_extraccion": _ratio(sum(f["ok"] for f in present), len(present)),
        "exactitud_por_campo": {k: _ratio(sum(v), len(v)) for k, v in sorted(by_field.items())},
        "abstencion_correcta": _ratio(sum(f["ok"] for f in abstain), len(abstain)),
        "falsos_ok_documentales": _ratio(
            sum(r["all_rules_pass"] for r in negatives), len(negatives)
        ),
        "contaminacion_de_listos": _ratio(
            sum(r["outcome"] == "NEGATIVE" for r in readies), len(readies)
        ),
        "completitud_de_camino_valido": _ratio(sum(r["all_rules_pass"] for r in valid), len(valid)),
        "deteccion_por_regla": {k: _ratio(sum(v), len(v)) for k, v in sorted(by_rule.items())},
        "errores_de_extraccion": sum(r["error"] is not None for r in runs),
        "tokens": {
            "input": sum(r["tokens"]["input"] for r in runs),
            "output": sum(r["tokens"]["output"] for r in runs),
        },
        "latencia_por_documento_ms": {
            "p50": round(statistics.median(lat), 1) if lat else None,
            "p95": round(lat[max(0, int(len(lat) * 0.95) - 1)], 1) if lat else None,
        },
        "costo_usd": None,
    }
    return out


def verdict(m: dict[str, Any]) -> str:
    def at_least(metric, target):
        return metric["value"] is not None and metric["value"] >= target

    ok = (
        m["errores_de_extraccion"] == 0
        and at_least(m["exactitud_de_extraccion"], THRESHOLDS["exactitud"])
        and at_least(m["abstencion_correcta"], THRESHOLDS["abstencion"])
        and m["falsos_ok_documentales"]["numerator"] == 0
        and at_least(m["completitud_de_camino_valido"], THRESHOLDS["completitud"])
    )
    return "PASS" if ok else "FAIL"


async def _run_all(cases: list[dict], extractor, settings: Settings, repeats: int) -> list[dict]:
    """Todos los casos en un mismo event loop.

    Un `asyncio.run()` por caso cierra el loop en el que el cliente HTTP del proveedor abrió sus
    conexiones: la primera llamada de cada caso fallaba con «Connection error».
    """
    return [
        await _run_case(c, extractor, settings, rep) for rep in range(1, repeats + 1) for c in cases
    ]


def run_real_ai(
    settings: Settings,
    *,
    out_dir: Path | None,
    split: str = "holdout",
    repeats: int = 3,
    extractor=None,
) -> dict[str, Any]:
    if extractor is None:
        if settings.extraction_provider == "fake" or settings.missing_llm_key(
            settings.extraction_provider
        ):
            summary = _not_run(
                "Configura EXTRACTION_PROVIDER=openai o anthropic, su modelo y su clave. "
                "La evaluación real no utiliza el extractor fake.",
                split,
                repeats,
            )
            if out_dir is not None:
                write_report(summary, out_dir)
            return summary
        from app.api.main import build_extractor

        extractor = build_extractor(settings)
    if getattr(extractor, "name", "") == "fake":
        raise ValueError("La pista real_ai no se ejecuta con el extractor de fixtures.")
    cases = [c for c in load_labels()["cases"] if c["split"] == split]
    runs = asyncio.run(_run_all(cases, extractor, settings, repeats))
    m = metrics(runs)
    summary = {
        "suite": "real_ai",
        "status": verdict(m),
        "split": split,
        "repeats": repeats,
        "generated_at": datetime.now(UTC).isoformat(),
        "versions": {
            "extractor": getattr(extractor, "name", "?"),
            "model": getattr(extractor, "_model", None),
            "prompt": PROMPT_VERSION,
        },
        "thresholds": THRESHOLDS,
        "metrics": m,
        "runs": runs,
    }
    if out_dir is not None:
        write_report(summary, out_dir)
    return summary


def write_report(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "real_ai_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    lines = [
        "# Informe de evaluación: pista de IA real",
        "",
        f"Estado: **{summary['status']}** · muestra `{summary['split']}` · "
        f"{summary['repeats']} repeticiones · {summary['generated_at']}",
        "",
    ]
    if summary["status"] == "NOT_RUN":
        lines += [
            f"Motivo: {summary['reason']}",
            "",
            "No hay resultados de calidad real que reportar. El dataset, las etiquetas y "
            "este runner están listos; ver `python -m scripts.run_evals real_ai`.",
            "",
        ]
    else:
        m = summary["metrics"]
        for name in (
            "exactitud_de_extraccion",
            "abstencion_correcta",
            "falsos_ok_documentales",
            "contaminacion_de_listos",
            "completitud_de_camino_valido",
        ):
            r = m[name]
            value = "N/A" if r["value"] is None else f"{r['value'] * 100:.1f} %"
            lines.append(
                f"- {name.replace('_', ' ')}: {r['numerator']}/{r['denominator']} ({value})"
            )
        lines.append(f"- Expedientes con errores de extracción: {m['errores_de_extraccion']}")
        lines += ["", "La muestra retenida es pequeña (S6): no demuestra desempeño productivo.", ""]
    (out_dir / "real_ai_summary.md").write_text("\n".join(lines), encoding="utf-8")
