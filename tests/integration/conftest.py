"""Integración contra PostgreSQL y MinIO reales.

Requiere DATABASE_URL (o TEST_DATABASE_URL) y las variables S3_* del usuario de aplicación.
Crea una base `<db>_test` desde cero y aplica las migraciones. Si falta configuración,
la suite falla: no se omite en silencio.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"Falta {name} para las pruebas de integración.", pytrace=False)
    return value


@pytest.fixture(scope="session")
def test_database_url() -> str:
    base = os.environ.get("TEST_DATABASE_URL") or _require("DATABASE_URL")
    url = make_url(base)
    test_url = url.set(database=f"{url.database.removesuffix('_test')}_test")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    admin.dispose()
    rendered = test_url.render_as_string(hide_password=False)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": rendered},
        check=True,
    )
    return rendered


@pytest.fixture(scope="session")
def demo_credentials(test_database_url, tmp_path_factory) -> dict[str, str]:
    from scripts.init_demo import init_demo

    path = tmp_path_factory.mktemp("creds") / "demo-credentials.json"
    created = init_demo(test_database_url, path, rotate=set())
    assert set(created) == {"cliente-ana", "cliente-beto", "asesor-1", "agente-servicio"}
    return created


@pytest.fixture(scope="session")
def settings(test_database_url, tmp_path_factory) -> Settings:
    return Settings(
        database_url=test_database_url,
        s3_endpoint_url=_require("S3_ENDPOINT_URL"),
        s3_bucket=_require("S3_BUCKET"),
        s3_access_key_id=_require("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=_require("S3_SECRET_ACCESS_KEY"),
        s3_namespace="test/",  # no se mezcla con los documentos de la demo
        trace_dir=tmp_path_factory.mktemp("traces"),
        langfuse_enabled=False,
    )
