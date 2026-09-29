"""Runner de la pista de IA real con dobles de prueba (no mide ningún modelo real)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.providers.extraction import ExtractionResult, ExtractionUnavailable, sanitize_extraction
from evals.real_ai import DATASET, load_labels, run_real_ai

SETTINGS = SimpleNamespace(max_file_bytes=10 * 1024 * 1024, max_document_pages=3,
                           max_image_pixels=20_000_000, extraction_provider="fake",
                           anthropic_api_key=None)  # fmt: skip
TRUTH = {d["sha256"]: d for c in load_labels()["cases"] for d in c["documents"]}


class Reader:
    """Lee la verdad por hash; `invent` rellena lo ausente con un valor falso y seguro."""

    name = "stub-reader"

    def __init__(self, invent: bool = False) -> None:
        self.invent = invent
        self.calls: list[tuple] = []

    async def extract(self, content, mime_type, declared_kind, page_count):
        self.calls.append((type(content), mime_type, declared_kind, page_count))
        doc = TRUTH[hashlib.sha256(content).hexdigest()]
        fields = {}
        for name, value in doc["truth"].items():
            if value is None and self.invent and name != "address_internal_number":
                value = "99999.00" if name == "income_amount" else "SEMIMONTHLY"
            # Un nulo legítimo (sin número interior) se lee con confianza; lo ausente, no.
            sure = value is not None or name == "address_internal_number"
            fields[name] = {"value": value, "confidence": 0.97 if sure else None,
                            "page": 1, "bbox": [0.1, 0.1, 0.9, 0.2],
                            "evidence_text": "" if value is None else str(value)}  # fmt: skip
        raw = {"detected_type": doc["kind"], "legible": doc["legible"], "fields": fields}
        return ExtractionResult(sanitize_extraction(raw, declared_kind, page_count), "stub", "m",
                                100, 20)  # fmt: skip


def test_dataset_shape_follows_s6():
    cases = load_labels()["cases"]
    assert len(cases) == 12 and sum(len(c["documents"]) for c in cases) == 36
    dev = [c for c in cases if c["split"] == "dev"]
    holdout = [c for c in cases if c["split"] == "holdout"]
    assert [c["outcome"] for c in dev].count("VALID") == 2 and len(dev) == 4
    assert [c["outcome"] for c in holdout].count("VALID") == 3 and len(holdout) == 8
    names = lambda group: {c["declared_profile"]["full_name"] for c in group}  # noqa: E731
    assert not names(dev) & names(holdout)  # separación por identidad
    assert {c["template"] for c in dev} == {"A"} and "B" in {c["template"] for c in holdout}
    for doc in (d for c in cases for d in c["documents"]):
        assert hashlib.sha256((DATASET / doc["file"]).read_bytes()).hexdigest() == doc["sha256"]


def test_perfect_reader_passes_and_every_negative_is_caught_by_its_rule():
    reader = Reader()
    summary = run_real_ai(SETTINGS, out_dir=None, split="holdout", repeats=2, extractor=reader)
    m = summary["metrics"]
    assert summary["status"] == "PASS"
    assert m["exactitud_de_extraccion"]["value"] == 1.0
    assert m["abstencion_correcta"]["value"] == 1.0
    assert m["falsos_ok_documentales"]["numerator"] == 0
    assert m["falsos_ok_documentales"]["denominator"] == 10  # 5 negativos × 2 repeticiones
    assert m["completitud_de_camino_valido"]["value"] == 1.0
    assert all(r["value"] == 1.0 for r in m["deteccion_por_regla"].values())
    assert len(summary["runs"]) == 16  # todas las repeticiones se reportan
    # El extractor solo recibe bytes, tipo MIME, tipo declarado y páginas: nunca etiquetas.
    assert {c[0] for c in reader.calls} == {bytes}


def test_inventing_reader_fails_abstention_and_produces_false_ok():
    summary = run_real_ai(SETTINGS, out_dir=None, split="holdout", repeats=1,
                          extractor=Reader(invent=True))  # fmt: skip
    m = summary["metrics"]
    assert summary["status"] == "FAIL"
    assert m["abstencion_correcta"]["value"] < 1.0
    assert m["falsos_ok_documentales"]["numerator"] >= 1


def test_provider_failure_on_negative_case_cannot_pass_quality_gate(tmp_path):
    class UnavailableReader(Reader):
        async def extract(self, content, *args):
            doc = TRUTH[hashlib.sha256(content).hexdigest()]
            if "income_amount" in doc["truth"] and doc["truth"]["income_amount"] is None:
                raise ExtractionUnavailable("synthetic timeout")
            return await super().extract(content, *args)

    summary = run_real_ai(
        SETTINGS, out_dir=tmp_path, split="holdout", repeats=1, extractor=UnavailableReader()
    )
    # Los campos que sí se leyeron son perfectos y todos los válidos avanzan. El caso
    # negativo queda bloqueado por falta de datos, no por una lectura evaluable.
    metrics = summary["metrics"]
    assert metrics["exactitud_de_extraccion"]["value"] == 1.0
    assert metrics["abstencion_correcta"]["value"] == 1.0
    assert metrics["completitud_de_camino_valido"]["value"] == 1.0
    assert metrics["falsos_ok_documentales"]["numerator"] == 0
    assert metrics["errores_de_extraccion"] == 1
    assert summary["status"] == "FAIL"
    assert "Expedientes con errores de extracción: 1" in (
        tmp_path / "real_ai_summary.md"
    ).read_text()


def test_not_run_without_real_extractor_and_refuses_fixtures(tmp_path):
    summary = run_real_ai(SETTINGS, out_dir=tmp_path)
    assert summary["status"] == "NOT_RUN" and summary["metrics"] == {}
    assert "NOT_RUN" in (tmp_path / "real_ai_summary.md").read_text()
    fake = SimpleNamespace(name="fake")
    with pytest.raises(ValueError, match="fixtures"):
        run_real_ai(SETTINGS, out_dir=None, extractor=fake)


def test_all_cases_run_in_one_event_loop():
    # Un loop por caso rompía el cliente HTTP real: la primera llamada de cada caso fallaba.
    import asyncio

    loops = set()

    class LoopReader(Reader):
        async def extract(self, *args):
            loops.add(id(asyncio.get_running_loop()))
            return await super().extract(*args)

    run_real_ai(SETTINGS, out_dir=None, split="holdout", repeats=2, extractor=LoopReader())
    assert len(loops) == 1
