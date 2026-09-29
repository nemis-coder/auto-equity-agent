"""Runner de la suite determinística e informe (TDD §8.1–§8.3)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.providers.extraction import PROMPT_VERSION
from evals.harness import Harness, Outcome
from evals.metrics import compute
from evals.scenarios import SCENARIOS, Scenario

TRACED = {"D04:base": "happy_path_sin_llave", "D05:base": "correccion_documental"}


def _git_commit() -> str | None:
    if os.environ.get("EVAL_COMMIT"):  # el contenedor no trae git
        return os.environ["EVAL_COMMIT"]
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def versions(settings: Settings) -> dict[str, Any]:
    return {
        "commit": _git_commit(),
        "policy_version": settings.policy_version,
        "extractor": "fake/fixture-v1",
        "extractor_prompt": PROMPT_VERSION,
        "agent": "conversation/server/graph.py con modelo guionado (scripted-chat-v1)",
        "clock": "2026-09-24T12:00:00Z (fijo)",
    }


def run_scenario(
    sc: Scenario, settings: Settings, creds: dict[str, str], trace_root: Path | None
) -> dict[str, Any]:
    trace_dir = None
    if trace_root is not None and sc.key in TRACED:
        trace_dir = trace_root / TRACED[sc.key]
        shutil.rmtree(trace_dir, ignore_errors=True)
    outcome = Outcome()
    started = time.monotonic()
    status, error = "PASS", None
    harness = Harness(
        settings,
        creds,
        extractor=sc.extractor() if sc.extractor else None,
        bureau_factory=sc.bureau,
        trace_dir=trace_dir,
    )
    with harness as h:
        try:
            sc.run(h, outcome)
        except Exception as exc:  # noqa: BLE001 - un escenario roto se reporta, no aborta
            status, error = "ERROR", "".join(traceback.format_exception_only(exc)).strip()[:500]
        snap = outcome.snapshot
        events = h.event_types(snap["case_id"]) if snap else []
        seen = [e for e in sc.forbidden_events if e in events]
        if sc.forbidden_events:
            outcome.check("trayectoria permitida (sin eventos prohibidos)", not seen, seen)
        outcome.facts.update(
            {
                "trajectory_ok": not seen,
                "reached_ready": "CASE_READY" in events,
                "bureau_called": "BUREAU_QUERIED" in events,
            }
        )
        tokens = h.usage(snap["case_id"]) if snap else {"input": 0, "output": 0}
        ledger = [tuple(r) for cid in h.case_ids for r in h.ledger_for_case(cid)]
        correlation = h.last_correlation
    actual_state = snap["workflow_state"] if snap else None
    if status == "PASS" and (
        not all(c.passed for c in outcome.checks) or actual_state != sc.ground_truth["state"]
    ):
        status = "FAIL"
    return {
        "scenario_id": sc.id,
        "variant": sc.variant,
        "title": sc.title,
        "mode": "deterministic",
        "ground_truth": sc.ground_truth,
        "tags": sc.tags,
        "actual": {
            "state": actual_state,
            "wait_reason": snap.get("wait_reason") if snap else None,
            "failed_rules": sorted(
                v["rule_id"] for v in (snap or {}).get("validations", []) if v["status"] != "PASS"
            ),
        },
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail} for c in outcome.checks
        ],
        "status": status,
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "tokens": tokens,
        "cost_usd": None,
        "trace_id": correlation,
        "facts": {k: v for k, v in outcome.facts.items()},
        "_ledger": ledger,
    }


def run_suite(
    settings: Settings,
    creds: dict[str, str],
    out_dir: Path | None = None,
    only: set[str] | None = None,
) -> dict[str, Any]:
    trace_root = out_dir / "traces" if out_dir else None
    results = [
        run_scenario(sc, settings, creds, trace_root)
        for sc in SCENARIOS
        if not only or sc.id in only
    ]
    seen_rows = {}
    for r in results:
        for provider, attempts, effects in r.pop("_ledger"):
            seen_rows[(r["scenario_id"], r["variant"], provider, attempts, effects)] = effects
    duplicates = sum(max(e - 1, 0) for e in seen_rows.values())
    summary = {
        "suite": "deterministic",
        "generated_at": datetime.now(UTC).isoformat(),
        "versions": versions(settings),
        "totals": {
            "scenarios": len({r["scenario_id"] for r in results}),
            "runs": len(results),
            "pass": sum(r["status"] == "PASS" for r in results),
            "fail": sum(r["status"] == "FAIL" for r in results),
            "error": sum(r["status"] == "ERROR" for r in results),
        },
        "metrics": compute(results, duplicates),
        "results": results,
    }
    if out_dir is not None:
        write_reports(summary, out_dir)
    return summary


def _pct(metric: dict) -> str:
    if metric.get("value") is None:
        return metric["status"]
    if "denominator" in metric:
        return f"{metric['numerator']}/{metric['denominator']} ({metric['value'] * 100:.1f} %)"
    return str(metric["value"])


def write_reports(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    t = summary["totals"]
    lines = [
        "# Informe de evaluación: suite determinística",
        "",
        f"Generado: {summary['generated_at']} · commit `{summary['versions']['commit']}`",
        "",
        "Pista determinística del TDD §8.1: proveedores simulados, extractor por fixtures y el "
        "grafo real del agente con un modelo guionado, contra PostgreSQL y MinIO reales. Mide "
        "reglas, flujo y controles del agente; **no mide la calidad de un modelo real**; esa es "
        "la pista `real_ai`.",
        "",
        f"**{t['pass']}/{t['runs']} ejecuciones PASS** ({t['scenarios']} escenarios base; "
        f"{t['fail']} FAIL, {t['error']} ERROR).",
        "",
        "## Métricas (§8.2)",
        "",
        "| Métrica | Resultado | Estado |",
        "| --- | --- | --- |",
    ]
    for name, metric in summary["metrics"].items():
        if name == "tiempo_por_escenario_ms":
            value = f"p50 {metric['p50']} ms · p95 {metric['p95']} ms"
        elif name == "tokens_y_costo":
            value = f"{metric['input']} entrada / {metric['output']} salida; costo N/A"
        else:
            value = metric.get("reason") or _pct(metric)
        lines.append(f"| {name.replace('_', ' ')} | {value} | {metric['status']} |")
    lines += [
        "",
        "## Escenarios",
        "",
        "| ID | Variante | Esperado | Real | Checks | Estado | ms |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for r in summary["results"]:
        ok = sum(c["passed"] for c in r["checks"])
        lines.append(
            f"| {r['scenario_id']} | {r['variant']} | {r['ground_truth']['state']} | "
            f"{r['actual']['state']} | {ok}/{len(r['checks'])} | {r['status']} | "
            f"{r['duration_ms']:.0f} |"
        )
    problems = [r for r in summary["results"] if r["status"] != "PASS"]
    if problems:
        lines += ["", "## Fallos", ""]
        for r in problems:
            failed = [c for c in r["checks"] if not c["passed"]]
            lines.append(
                f"- **{r['scenario_id']} {r['variant']}**: {r['error'] or ''} "
                + "; ".join(f"{c['name']} ({c['detail']})" for c in failed)
            )
    lines += ["", "Versiones: " + ", ".join(f"{k}={v}" for k, v in summary["versions"].items()), ""]
    (out_dir / "eval_summary.md").write_text("\n".join(lines), encoding="utf-8")
