"""Ejecuta las suites de evaluación del TDD §8 y escribe `reports/`.

    python -m scripts.run_evals deterministic [--out reports]
    python -m scripts.run_evals real_ai [--split holdout] [--repeats 3] [--out reports]

La suite determinística crea una base `<db>_eval` desde cero (migraciones + actores demo) para
no tocar los datos de la demo. Usa las variables S3_* y DATABASE_URL del entorno.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402


def fresh_eval_database(base_url: str) -> str:
    url = make_url(base_url)
    eval_url = url.set(database=f"{url.database.removesuffix('_eval')}_eval")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{eval_url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{eval_url.database}"'))
    admin.dispose()
    rendered = eval_url.render_as_string(hide_password=False)
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": rendered},
        check=True,
    )
    return rendered


def deterministic(out: Path) -> int:
    from evals.runner import run_suite
    from scripts.init_demo import init_demo

    url = fresh_eval_database(os.environ["DATABASE_URL"])
    with tempfile.TemporaryDirectory() as tmp:
        creds = init_demo(url, Path(tmp) / "creds.json", rotate=set())
        # Las trazas de los escenarios no seleccionados se descartan con el temporal.
        settings = Settings(
            database_url=url, langfuse_enabled=False, trace_dir=Path(tmp), s3_namespace="eval/"
        )
        summary = run_suite(settings, creds, out_dir=out)
    t = summary["totals"]
    print(f"{t['pass']}/{t['runs']} PASS · informe en {out}/eval_summary.md")
    return 0 if t["pass"] == t["runs"] else 1


def real_ai(out: Path, split: str, repeats: int) -> int:
    from evals.real_ai import run_real_ai

    summary = run_real_ai(Settings(), out_dir=out, split=split, repeats=repeats)
    print(f"real_ai: {summary['status']} · informe en {out}/real_ai_summary.md")
    if summary["status"] == "NOT_RUN":
        print(f"Motivo: {summary['reason']}")
        return 2
    return 0 if summary["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=["deterministic", "real_ai"])
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--split", choices=["dev", "holdout"], default="holdout")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.suite == "deterministic":
        return deterministic(args.out)
    return real_ai(args.out, args.split, args.repeats)


if __name__ == "__main__":
    raise SystemExit(main())
