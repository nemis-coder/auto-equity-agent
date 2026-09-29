"""Harness de la suite determinística (TDD §8.1, pista 1).

Cada escenario corre contra una app nueva (FastAPI + PostgreSQL + MinIO reales) con reloj de
negocio fijo, proveedores simulados y, para el agente, su grafo real con un modelo guionado
(`evals/chat_agent.py`). El harness solo usa la API HTTP; la
base de datos se consulta únicamente para leer evidencia (ledger del mock, contadores), nunca
para fijar un estado.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api.main import create_app
from app.config import Settings
from app.domain.clock import FakeClock
from app.providers.extraction import ExtractionResult, FakeExtractionProvider

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "fixtures" / "documents"
FIXED = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

VEHICLE = {
    "owned_by_customer": True,
    "blocking_debt": False,
    "has_second_key": True,
    "vehicle_ref": "ABC-123-XYZ",
    "make": "Nissan",
    "model": "Versa",
    "year": 2020,
}
PROFILE = {
    "full_name": "Ana Prueba López",
    "address": {
        "street": "Calle Demo",
        "external_number": "123",
        "internal_number": None,
        "neighborhood": "Colonia Ejemplo",
        "municipality": "Ciudad de México",
        "state": "CDMX",
        "postal_code": "00000",
        "country": "MX",
    },
    "employment": "SALARIED",
    "employer_or_activity": "Empresa Sintética",
    "income": {"amount": "20000.00", "currency": "MXN", "period": "MONTHLY", "basis": "NET"},
}
HAPPY = {
    "IDENTITY": "identity_ana.png",
    "PAYSLIP": "payslip_ana.pdf",
    "VEHICLE_OWNERSHIP": "ownership_ana.pdf",
}


class MutatingExtractor(FakeExtractionProvider):
    """Extractor fake que altera la lectura de archivos concretos (variantes sin fixture)."""

    def __init__(self, mutations: dict[str, Callable[[dict[str, Any]], None]]) -> None:
        super().__init__()
        self._mutations = {
            hashlib.sha256((DOCS / name).read_bytes()).hexdigest(): fn
            for name, fn in mutations.items()
        }

    async def extract(self, content, mime_type, declared_kind, page_count) -> ExtractionResult:
        result = await super().extract(content, mime_type, declared_kind, page_count)
        mutate = self._mutations.get(hashlib.sha256(content).hexdigest())
        if mutate is None:
            return result
        extraction = copy.deepcopy(result.extraction)
        mutate(extraction)
        return ExtractionResult(extraction, result.provider, "fixture-v1+variante")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Outcome:
    """Lo que el escenario observó; el runner lo convierte en fila del informe."""

    snapshot: dict[str, Any] | None = None
    checks: list[Check] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)  # insumos de métricas §8.2
    correlation_id: str | None = None

    def check(self, name: str, condition: bool, detail: Any = "") -> bool:
        self.checks.append(Check(name, bool(condition), "" if condition else str(detail)[:300]))
        return bool(condition)


def headers(token: str, *, version: int | None = None, key: bool = True) -> dict[str, str]:
    out = {"Authorization": f"Bearer {token}"}
    if key:
        out["Idempotency-Key"] = f"eval-{uuid.uuid4()}"
    if version is not None:
        out["If-Match"] = str(version)
    return out


class Harness:
    def __init__(
        self,
        settings: Settings,
        credentials: dict[str, str],
        *,
        extractor=None,
        bureau_factory=None,
        trace_dir: Path | None = None,
    ) -> None:
        self.settings = settings.model_copy(
            update={
                "rate_limit_per_minute": 10_000,
                "read_rate_limit_per_minute": 10_000,
                "provider_retry_delays": [0.0, 0.0],
                **({"trace_dir": trace_dir} if trace_dir else {}),
            }
        )
        self.creds = credentials
        self.clock = FakeClock(FIXED)
        self._extractor = extractor
        self._bureau_factory = bureau_factory
        self.engine = create_engine(settings.database_url)
        self.client: TestClient | None = None
        self.last_correlation: str | None = None
        self.case_ids: list[str] = []

    # --- ciclo de vida -------------------------------------------------------------------

    def __enter__(self) -> Harness:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
        self.engine.dispose()

    def start(self) -> None:
        app = create_app(self.settings, clock=self.clock, extractor=self._extractor)
        self.client = TestClient(app)
        self.client.__enter__()
        if self._bureau_factory is not None:
            services = self.client.app.state.services
            services.bureau = self._bureau_factory(services.sessionmaker)

    def stop(self) -> None:
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None

    def restart(self) -> None:
        """Simula un reinicio del proceso API: memoria nueva, mismo PostgreSQL y MinIO."""
        self.stop()
        self.start()

    # --- HTTP ----------------------------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.last_correlation = response.headers.get("x-correlation-id")
        return response

    def token(self, who: str) -> str:
        return self.creds[who]

    def create(self, who: str = "cliente-ana") -> dict:
        r = self._send("POST", "/cases", headers=headers(self.token(who)))
        assert r.status_code in (200, 201), r.text
        self.case_ids.append(r.json()["case_id"])
        return r.json()

    def get(self, case_id: str, who: str = "cliente-ana"):
        return self._send("GET", f"/cases/{case_id}", headers=headers(self.token(who), key=False))

    def snap(self, case_id: str, who: str = "cliente-ana") -> dict:
        r = self.get(case_id, who)
        assert r.status_code == 200, r.text
        return r.json()

    def propose(self, snap: dict, group: str, fields: dict, who: str = "cliente-ana"):
        return self._send(
            "POST",
            f"/cases/{snap['case_id']}/declaration-proposals",
            headers=headers(self.token(who), version=snap["case_version"]),
            json={"group": group, "fields": fields},
        )

    def confirm(self, snap: dict, who: str = "cliente-ana"):
        pending = snap["pending_action"]
        return self._send(
            "POST",
            f"/cases/{snap['case_id']}/confirmations",
            headers=headers(self.token(who), version=snap["case_version"]),
            json={
                "pending_action_id": pending["pending_action_id"],
                "payload_hash": pending["payload_hash"],
            },
        )

    def declare(self, snap: dict, group: str, fields: dict) -> dict:
        r = self.propose(snap, group, fields)
        assert r.status_code == 200, r.text
        r = self.confirm(r.json())
        assert r.status_code == 200, r.text
        return r.json()

    def select(self, snap: dict, term: int = 24, cash: str = "50000.00", who: str = "cliente-ana"):
        """Elige la opción del cotizador con ese plazo y efectivo (o la primera vigente)."""
        offers = [o for o in snap["offers"] if not o["expired"]]
        offer = next(
            (o for o in offers if o["term_months"] == term and o["cash_amount"] == cash),
            offers[0] if offers else {"offer_id": "none", "display_hash": "none"},
        )
        return self._send(
            "POST",
            f"/cases/{snap['case_id']}/selections",
            headers=headers(self.token(who), version=snap["case_version"]),
            json={"offer_id": offer["offer_id"], "displayed_offer_hash": offer["display_hash"]},
        )

    def upload(
        self,
        snap: dict,
        kind: str,
        filename: str,
        *,
        supersedes=None,
        who: str = "cliente-ana",
        key: str | None = None,
    ):
        hdrs = headers(self.token(who), version=snap["case_version"])
        if key:
            hdrs["Idempotency-Key"] = key
        data = {"declared_type": kind}
        if supersedes:
            data["supersedes_id"] = supersedes
        return self._send(
            "POST",
            f"/cases/{snap['case_id']}/documents",
            headers=hdrs,
            files={"file": (filename, (DOCS / filename).read_bytes(), "application/octet-stream")},
            data=data,
        )

    def upload_all(self, snap: dict, files: dict[str, str]) -> dict:
        for kind, name in files.items():
            r = self.upload(snap, kind, name)
            assert r.status_code == 201, r.text
            snap = r.json()
        return snap

    def chat(self, who: str = "cliente-ana"):
        """Un hilo nuevo con el agente de la conversación (el mismo grafo que usa el cliente)."""
        from evals.chat_agent import ChatSession

        return ChatSession(self.client, self.token(who))

    def run(self, snap: dict, who: str = "cliente-ana", key: str | None = None):
        hdrs = headers(self.token(who))
        if key:
            hdrs["Idempotency-Key"] = key
        return self._send("POST", f"/cases/{snap['case_id']}/runs", headers=hdrs)

    def resolve(self, snap: dict, body: dict, *, who: str = "asesor-1"):
        view = self.snap(snap["case_id"], who)
        return self._send(
            "POST",
            f"/cases/{snap['case_id']}/reviews/{view['open_review']['review_id']}/resolutions",
            headers=headers(self.token(who), version=view["case_version"]),
            json=body,
        )

    def events(self, case_id: str) -> list[dict]:
        r = self._send(
            "GET", f"/cases/{case_id}/events", headers=headers(self.token("asesor-1"), key=False)
        )
        return r.json()["items"]

    def event_types(self, case_id: str) -> list[str]:
        return [e["event_type"] for e in self.events(case_id)]

    # --- flujos compuestos ---------------------------------------------------------------

    def to_simulation(self, vehicle: dict | None = None, profile: dict | None = None) -> dict:
        snap = self.create()
        snap = self.declare(snap, "vehicle", vehicle or VEHICLE)
        return self.declare(snap, "profile", profile or PROFILE)

    def to_documents(self, vehicle: dict | None = None, profile: dict | None = None) -> dict:
        snap = self.to_simulation(vehicle, profile)
        r = self.select(snap)
        assert r.status_code == 200, r.text
        return r.json()

    # --- evidencia de solo lectura -------------------------------------------------------

    def sql(self, query: str, **params) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(text(query), params))

    def usage(self, case_id: str) -> dict[str, int]:
        row = self.sql("SELECT tokens_in, tokens_out FROM cases WHERE id = :c", c=case_id)[0]
        return {"input": row.tokens_in, "output": row.tokens_out}

    def ledger_for_case(self, case_id: str) -> list[Any]:
        """Filas del ledger del proveedor simulado cuyas claves pertenecen al caso."""
        return self.sql(
            "SELECT l.provider, l.attempts, l.effects FROM mock_provider_ledger l "
            "JOIN operations o ON o.idempotency_key = l.idempotency_key "
            "WHERE o.case_id = :c",
            c=case_id,
        )


def failed_rules(snap: dict) -> dict[str, str]:
    return {
        v["rule_id"]: v["reason_code"] for v in snap.get("validations", []) if v["status"] != "PASS"
    }
