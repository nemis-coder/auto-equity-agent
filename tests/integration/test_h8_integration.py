"""H8: reconciliación de almacenamiento, caída de Langfuse y reloj visible para los scripts."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import build_store, create_app
from app.domain.clock import FakeClock
from app.observability.traces import JsonlSink, Tracer
from app.persistence.db import make_engine, make_sessionmaker
from app.storage.interfaces import StorageError
from app.tools.storage_reconcile import reconcile
from tests.integration.test_h2_cases import FIXED, Api, h
from tests.integration.test_h4_documents import HAPPY, _settings, to_documents, upload_all
from tests.security.test_telemetry_privacy import _BrokenClient


async def test_reconcile_deletes_only_old_orphans_and_keeps_evidence(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        snap = upload_all(
            Api(c, demo_credentials["cliente-ana"]),
            to_documents(Api(c, demo_credentials["cliente-ana"])),
            HAPPY,
        )
    prefix = f"cases/{snap['case_id']}/"
    store = build_store(settings)
    # Objeto huérfano: la carga escribió los bytes y la fila nunca se guardó.
    content = f"huérfano {uuid.uuid4()}".encode()
    sha = hashlib.sha256(content).hexdigest()
    orphan = f"{prefix}documents/{sha}"
    await store.put(orphan, content, sha)

    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    try:
        recent = await reconcile(sm, store, prefix=prefix)  # gracia de 1 hora
        assert recent.orphans == [] and recent.recent_unreferenced == [orphan]
        later = datetime.now(UTC) + timedelta(hours=2)
        report = await reconcile(sm, store, prefix=prefix, now=later, delete=True)
    finally:
        await engine.dispose()
    assert report.orphans == [orphan] and report.deleted == [orphan]
    assert report.referenced == 3 and report.missing_objects == []
    with pytest.raises(StorageError):
        await store.get(orphan, sha)
    # La evidencia referenciada sigue intacta y verificable por hash.
    ident = next(d for d in snap["documents"] if d["slot"] == "IDENTITY")
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        r = c.get(
            f"/cases/{snap['case_id']}/documents/{ident['document_id']}/content",
            headers=h(demo_credentials["cliente-ana"], key=False),
        )
    assert r.status_code == 200


def test_langfuse_outage_never_blocks_the_business_flow(settings, demo_credentials, tmp_path):
    tracer = Tracer(sink=JsonlSink(tmp_path), langfuse_client=_BrokenClient())
    app = create_app(_settings(settings), clock=FakeClock(FIXED), tracer=tracer)
    with TestClient(app) as c:
        ready = c.get("/readyz").json()
        assert ready["status"] == "ready"
        assert ready["checks"]["telemetry"] == "langfuse_configured"
        api = Api(c, demo_credentials["cliente-ana"])
        snap = upload_all(api, to_documents(api), HAPPY)
        ready = c.get("/readyz").json()
        assert ready["status"] == "ready" and ready["checks"]["telemetry"] == "export_error"
    assert snap["workflow_state"] == "READY_FOR_FINANCIAL"
    assert tracer.export_errors > 0  # cada span falló al exportar y el negocio siguió
    assert list(tmp_path.glob("*.jsonl"))  # respaldo local intacto


def test_readyz_exposes_business_date_for_demo_scripts(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        body = c.get("/readyz").json()
    assert body["clock"]["business_date"] == "2026-09-24"


async def test_namespaces_keep_test_objects_out_of_the_demo_listing(settings):
    store = build_store(settings)
    content = f"aislado {uuid.uuid4()}".encode()
    sha = hashlib.sha256(content).hexdigest()
    key = f"cases/{uuid.uuid4()}/documents/{sha}"
    await store.put(key, content, sha)
    demo = build_store(settings.model_copy(update={"s3_namespace": ""}))
    assert key in {k for k, _ in await store.list_objects(key)}
    assert key not in {k for k, _ in await demo.list_objects(key)}
    await store.delete(key)
