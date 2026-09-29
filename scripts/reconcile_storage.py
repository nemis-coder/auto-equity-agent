"""Reconcilia documentos de PostgreSQL con los objetos de MinIO (TDD §6.9).

    python -m scripts.reconcile_storage                    # solo reporta
    python -m scripts.reconcile_storage --delete           # borra huérfanos con más de 60 min
    python -m scripts.reconcile_storage --delete --grace-minutes 0

Sale con código 2 si hay filas cuyo objeto falta (requiere investigación).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.main import build_store  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.persistence.db import make_engine, make_sessionmaker  # noqa: E402
from app.tools.storage_reconcile import reconcile  # noqa: E402


async def _main(delete: bool, grace_minutes: float) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        report = await reconcile(
            make_sessionmaker(engine),
            build_store(settings),
            grace=timedelta(minutes=grace_minutes),
            delete=delete,
        )
    finally:
        await engine.dispose()
    print(json.dumps(report.as_dict(), indent=2))
    return 2 if report.missing_objects else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--grace-minutes", type=float, default=60.0)
    args = parser.parse_args()
    return asyncio.run(_main(args.delete, args.grace_minutes))


if __name__ == "__main__":
    raise SystemExit(main())
