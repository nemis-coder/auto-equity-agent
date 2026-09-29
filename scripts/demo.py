"""Demos de regresión por HTTP (TDD §11.3). Complementan el recorrido del chat; no lo sustituyen.

    docker compose exec api python scripts/demo.py --scenario happy_path
    docker compose exec api python scripts/demo.py --scenario vehicle_not_owned
    docker compose exec api python scripts/demo.py --scenario document_income_mismatch
    docker compose exec api python scripts/demo.py --scenario missing_second_key
    docker compose exec api python scripts/demo.py --scenario all

Cada ejecución crea un caso nuevo y recorre los mismos endpoints que la UI: confirmaciones,
selección y carga real de archivos. Las confirmaciones y la selección provienen de este guion
del cliente simulado; sus claves de idempotencia llevan el prefijo `demo-script-` para dejar
registrada esa procedencia en `operations` y no confundirla con una elección del agente.
Solo usa la biblioteca estándar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "fixtures" / "documents"
FIXTURE_DATE = "2026-09-24"

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


class Demo:
    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base.rstrip("/"), token

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        version: int | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        if method == "POST":
            headers["Idempotency-Key"] = f"demo-script-{uuid.uuid4()}"
        if version is not None:
            headers["If-Match"] = str(version)
        if content_type:
            headers["Content-Type"] = content_type
        for _ in range(20):
            req = urllib.request.Request(  # noqa: S310 - URL de la API local configurada
                self.base + path, data=body, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:  # noqa: S310
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read() or b"{}")
                if exc.code == 429:  # límite por actor: se espera como lo haría una persona
                    time.sleep(5)
                    continue
                raise SystemExit(
                    f"{method} {path} → {exc.code} {payload.get('code')}: {payload.get('message')}"
                ) from None
        raise SystemExit("Límite de peticiones persistente; intenta más tarde.")

    def json(self, method: str, path: str, data: dict, version: int | None = None) -> dict:
        return self.request(
            method,
            path,
            body=json.dumps(data).encode(),
            content_type="application/json",
            version=version,
        )

    def declare(self, snap: dict, group: str, fields: dict) -> dict:
        cid = snap["case_id"]
        snap = self.json(
            "POST",
            f"/cases/{cid}/declaration-proposals",
            {"group": group, "fields": fields},
            snap["case_version"],
        )
        p = snap["pending_action"]
        return self.json(
            "POST",
            f"/cases/{cid}/confirmations",
            {"pending_action_id": p["pending_action_id"], "payload_hash": p["payload_hash"]},
            snap["case_version"],
        )

    def select(self, snap: dict, term: int = 24, cash: str = "50000.00") -> dict:
        """El cliente simulado elige una opción del cotizador: plazo y efectivo."""
        offer = next(
            o for o in snap["offers"]
            if o["term_months"] == term and o["cash_amount"] == cash and not o["expired"]
        )  # fmt: skip
        return self.json(
            "POST",
            f"/cases/{snap['case_id']}/selections",
            {"offer_id": offer["offer_id"], "displayed_offer_hash": offer["display_hash"]},
            snap["case_version"],
        )

    def upload(self, snap: dict, kind: str, filename: str) -> dict:
        boundary = uuid.uuid4().hex
        content = (DOCS / filename).read_bytes()
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="declared_type"\r\n\r\n'
                f'{kind}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.request(
            "POST",
            f"/cases/{snap['case_id']}/documents",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            version=snap["case_version"],
        )


def step(text: str) -> None:
    print(f"  · {text}")


def check(condition: bool, text: str) -> bool:
    print(f"  {'✔' if condition else '✘'} {text}")
    return condition


def to_offers(d: Demo, vehicle: dict) -> dict:
    snap = d.request("POST", "/cases")
    step(f"caso {snap['case_id'][:8]} creado")
    snap = d.declare(snap, "vehicle", vehicle)
    step(f"respuestas del auto confirmadas → {snap['workflow_state']}")
    if snap["workflow_state"] == "REJECTED":
        return snap
    snap = d.declare(snap, "profile", PROFILE)
    step(f"perfil confirmado → {snap['workflow_state']} con {len(snap['offers'])} ofertas")
    return snap


def upload_all(d: Demo, snap: dict, files: dict[str, str]) -> dict:
    for kind, name in files.items():
        snap = d.upload(snap, kind, name)
        step(f"{name} cargado → {snap['workflow_state']}")
    return snap


def happy_path(d: Demo) -> bool:
    snap = d.select(to_offers(d, VEHICLE))
    step("opción de 24 meses elegida por el guion del cliente")
    snap = upload_all(d, snap, HAPPY)
    return check(
        snap["workflow_state"] == "READY_FOR_FINANCIAL",
        "expediente listo para revisión de la financiera (no es aprobación)",
    )


def vehicle_not_owned(d: Demo) -> bool:
    snap = to_offers(d, {"owned_by_customer": False})
    return check(
        snap["workflow_state"] == "REJECTED"
        and snap["eligibility"]["rejection_reasons"] == ["VEHICLE_NOT_OWNED"],
        "rechazo por titularidad, sin consultar el Buró",
    )


def document_income_mismatch(d: Demo) -> bool:
    snap = d.select(to_offers(d, VEHICLE))
    snap = upload_all(d, snap, {**HAPPY, "PAYSLIP": "payslip_ana_low.pdf"})
    reasons = [r["reason_code"] for r in snap["next_action"].get("reasons", [])]
    return check(
        snap["workflow_state"] == "NEEDS_CORRECTION" and "INCOME_MISMATCH" in reasons,
        "corrección pedida: el ingreso no coincide (tolerancia 10 %)",
    )


def missing_second_key(d: Demo) -> bool:
    snap = to_offers(d, {**VEHICLE, "has_second_key": False})
    offer = next(
        o for o in snap["offers"] if o["term_months"] == 24 and o["cash_amount"] == "50000.00"
    )
    ok = check((snap["key_quote"] or {}).get("amount") == "3000.00", "llave cotizada: $3,000.00")
    ok &= check(
        (offer["financed_principal"], offer["regular_payment"], offer["last_payment"])
        == ("53000.00", "2802.17", "2802.11"),
        "capital $53,000.00; cuota $2,802.17; último pago $2,802.11 (§6.5)",
    )
    snap = upload_all(d, d.select(snap), HAPPY)
    return ok & check(snap["workflow_state"] == "READY_FOR_FINANCIAL", "expediente listo")


SCENARIOS = {
    "happy_path": happy_path,
    "vehicle_not_owned": vehicle_not_owned,
    "document_income_mismatch": document_income_mismatch,
    "missing_second_key": missing_second_key,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], required=True)
    parser.add_argument("--api", default=os.environ.get("DEMO_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--credentials",
        default=os.environ.get("DEMO_CREDENTIALS_PATH", "/data/demo-credentials.json"),
    )
    args = parser.parse_args()
    ready = None
    for _ in range(30):  # la API puede estar arrancando
        try:
            with urllib.request.urlopen(args.api + "/readyz", timeout=10) as resp:  # noqa: S310
                ready = json.load(resp)
            break
        except OSError:
            time.sleep(2)
    if ready is None:
        print(f"La API no responde en {args.api}; revisa `docker compose ps`.")
        return 2
    if (ready.get("clock") or {}).get("business_date") != FIXTURE_DATE:
        print(
            f"El reloj de negocio del servidor es {ready.get('clock')}; los documentos de "
            f"prueba están fechados para {FIXTURE_DATE}. Configura CLOCK_MODE=fixed y "
            f"FIXED_NOW=2026-09-24T12:00:00Z en .env y recrea la API."
        )
        return 2
    token = json.loads(Path(args.credentials).read_text())["cliente-ana"]
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = {}
    for name in names:
        print(f"\n▶ {name}")
        results[name] = SCENARIOS[name](Demo(args.api, token))
    print("\n" + " · ".join(f"{k}: {'OK' if v else 'FALLÓ'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
