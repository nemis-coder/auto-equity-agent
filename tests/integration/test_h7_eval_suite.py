"""La suite determinística completa (TDD §8.1) corre contra PostgreSQL y MinIO reales."""

from __future__ import annotations

import json

from evals.runner import run_suite
from evals.scenarios import SCENARIOS


def test_deterministic_suite_passes_all_scenarios_and_metrics(settings, demo_credentials, tmp_path):
    summary = run_suite(settings, demo_credentials, out_dir=tmp_path)
    problems = [
        (r["scenario_id"], r["variant"], r["error"], [c for c in r["checks"] if not c["passed"]])
        for r in summary["results"]
        if r["status"] != "PASS"
    ]
    assert not problems, json.dumps(problems, ensure_ascii=False, indent=1, default=str)
    assert summary["totals"]["scenarios"] == 25
    assert summary["totals"]["runs"] == len(SCENARIOS)
    failing = {k: m for k, m in summary["metrics"].items() if m["status"] == "FAIL"}
    assert not failing, failing
    # Lo que esta pista no mide queda NOT_RUN, nunca PASS.
    assert summary["metrics"]["exactitud_de_extraccion"]["status"] == "NOT_RUN"
    report = json.loads((tmp_path / "eval_summary.json").read_text())
    assert all(r["trace_id"] for r in report["results"])
    assert (tmp_path / "eval_summary.md").read_text().count("| D") >= 25
    traces = sorted(p.parent.name for p in (tmp_path / "traces").rglob("*.jsonl"))
    assert traces == ["correccion_documental", "happy_path_sin_llave"]
