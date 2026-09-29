"""El estado de una evaluación también debe reflejarse en la salida del proceso."""

from types import SimpleNamespace

import pytest

from evals import real_ai as evaluation
from scripts import run_evals


@pytest.mark.parametrize("status,expected", [("PASS", 0), ("FAIL", 1), ("NOT_RUN", 2)])
def test_real_ai_exit_code_reflects_the_report(monkeypatch, tmp_path, capsys, status, expected):
    calls = []
    settings = object()
    monkeypatch.setattr(run_evals, "Settings", lambda: settings)

    def run(config, **kwargs):
        calls.append((config, kwargs))
        return {"status": status, "reason": "Configura un extractor real."}

    monkeypatch.setattr(evaluation, "run_real_ai", run)
    assert run_evals.real_ai(tmp_path, "dev", 2) == expected
    assert calls == [(settings, {"out_dir": tmp_path, "split": "dev", "repeats": 2})]
    output = capsys.readouterr().out
    assert f"real_ai: {status}" in output
    if status == "NOT_RUN":
        assert "Configura un extractor real." in output


def test_unconfigured_cli_writes_not_run_report_and_returns_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run_evals, "Settings", lambda: SimpleNamespace(extraction_provider="fake")
    )
    monkeypatch.setattr(
        run_evals.sys, "argv", ["run_evals", "real_ai", "--out", str(tmp_path)]
    )
    assert run_evals.main() == 2
    assert "NOT_RUN" in (tmp_path / "real_ai_summary.md").read_text()
