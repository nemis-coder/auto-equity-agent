"""Pantalla del asesor en el navegador (TDD §6.8 y §8.3): bandeja, detalle, documentos y
resoluciones de D15; acceso solo con token de asesor y sesión limitada a la pestaña.

El cliente llega a revisión humana por la API (dos rondas de corrección sin éxito, D15).
Con esta combinación de Playwright y Chromium, `expect()` no es fiable (bitácora H3): se espera
con `locator.wait_for`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from tests.e2e_ui.conftest import ADVISOR

DOCS = Path(__file__).resolve().parents[2] / "fixtures" / "documents"
T = 30_000
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
    },  # fmt: skip
    "employment": "SALARIED",
    "employer_or_activity": "Empresa Sintética",
    "income": {"amount": "20000.00", "currency": "MXN", "period": "MONTHLY", "basis": "NET"},
}


def shot(page, name: str) -> None:
    """Captura del resultado (sin secretos: el token nunca se muestra en pantalla)."""
    folder = os.environ.get("E2E_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


def see(locator) -> None:
    try:
        locator.first.wait_for(state="visible", timeout=T)
    except Exception:
        shot(locator.page, "_fallo")
        raise


class Customer:
    """El cliente por la API, como lo haría el agente conversacional."""

    def __init__(self, http, token: str) -> None:
        self.http, self.token = http, token

    def _headers(self, version: int | None = None, key: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if key:
            headers["Idempotency-Key"] = f"e2e-{uuid.uuid4()}"
        if version is not None:
            headers["If-Match"] = str(version)
        return headers

    def _ok(self, response) -> dict:
        assert response.ok, response.text()
        return response.json()

    def post(self, path: str, snap: dict | None = None, body: dict | None = None) -> dict:
        version = snap["case_version"] if snap else None
        return self._ok(self.http.post(path, headers=self._headers(version), data=body))

    def get(self, path: str) -> dict:
        return self._ok(self.http.get(path, headers=self._headers(key=False)))

    def declare(self, snap: dict, group: str, fields: dict) -> dict:
        base = f"/cases/{snap['case_id']}"
        snap = self.post(f"{base}/declaration-proposals", snap, {"group": group, "fields": fields})
        pending = snap["pending_action"]
        return self.post(f"{base}/confirmations", snap, {
            "pending_action_id": pending["pending_action_id"],
            "payload_hash": pending["payload_hash"],
        })  # fmt: skip

    def upload(self, snap: dict, kind: str, filename: str) -> dict:
        file = {"name": filename, "mimeType": "application/octet-stream",
                "buffer": (DOCS / filename).read_bytes()}  # fmt: skip
        return self._ok(self.http.post(
            f"/cases/{snap['case_id']}/documents",
            headers=self._headers(snap["case_version"]),
            multipart={"declared_type": kind, "file": file},
        ))  # fmt: skip

    def to_review(self) -> dict:
        """D15: dos rondas de corrección documental sin éxito abren una revisión humana."""
        snap = self.post("/cases")
        snap = self.declare(snap, "vehicle", VEHICLE)
        snap = self.declare(snap, "profile", PROFILE)
        offer = next(
            o
            for o in snap["offers"]
            if not o["expired"] and o["term_months"] == 24 and o["cash_amount"] == "50000.00"
        )
        snap = self.post(f"/cases/{snap['case_id']}/selections", snap, {
            "offer_id": offer["offer_id"], "displayed_offer_hash": offer["display_hash"],
        })  # fmt: skip
        for kind, name in (("IDENTITY", "identity_ana.png"), ("PAYSLIP", "payslip_ana_low.pdf"),
                           ("VEHICLE_OWNERSHIP", "ownership_ana.pdf")):  # fmt: skip
            snap = self.upload(snap, kind, name)
        snap = self.upload(snap, "PAYSLIP", "payslip_ana_lowconf.pdf")
        assert snap["workflow_state"] == "HUMAN_REVIEW", snap["workflow_state"]
        return snap


def login(page, token: str) -> None:
    page.goto(ADVISOR)
    page.get_by_label("Token").fill(token)
    page.get_by_role("button", name="Entrar").click()


def advisor(new_page, token: str):
    page = new_page(height=1800)
    login(page, token)
    see(page.get_by_role("heading", name="Bandeja de revisiones"))
    return page


def open_case(page, case_id: str) -> None:
    """Abre el caso desde la bandeja y espera a que el detalle sea el de ese caso."""
    see(page.get_by_role("button", name=case_id[:8]))
    page.get_by_role("button", name=case_id[:8]).click()
    page.wait_for_function(
        "id => document.getElementById('case-select').value === id", arg=case_id, timeout=T
    )
    see(page.get_by_text("Revisión abierta:"))


def test_d15_advisor_resolves_from_inbox_with_a_human_reading(new_page, http, creds):
    snap = Customer(http, creds["cliente-ana"]).to_review()
    page = advisor(new_page, creds["asesor-1"])
    open_case(page, snap["case_id"])
    see(page.get_by_text("Dos rondas de corrección documental sin éxito"))
    page.get_by_role("tab", name="Registrar lectura").click()
    page.get_by_label("Documento").select_option(label="Recibo de nómina")
    page.get_by_label("monto del ingreso (confianza 0.89)").check()
    page.get_by_label("Motivo de la lectura").fill("Cifra legible en el original")
    page.get_by_role("button", name="Registrar lectura humana").click()
    see(page.get_by_text("Resolución registrada."))
    see(page.get_by_text("Expediente listo para revisión de la financiera."))
    shot(page, "asesor_resolucion_d15")
    case = Customer(http, creds["cliente-ana"]).get(f"/cases/{snap['case_id']}")
    assert case["workflow_state"] == "READY_FOR_FINANCIAL"


def test_advisor_requests_a_correction_and_downloads_a_document(new_page, http, creds):
    customer = Customer(http, creds["cliente-ana"])
    snap = customer.to_review()
    page = advisor(new_page, creds["asesor-1"])
    open_case(page, snap["case_id"])
    with page.expect_download() as download:
        page.get_by_role("button", name="Descargar").first.click()
    kind = download.value.suggested_filename.split("-")[0]
    assert kind in ("identity", "payslip", "vehicle_ownership")
    page.get_by_label("Comprobante de ingresos").check()
    page.get_by_label("Mensaje para el cliente").fill("Sube el recibo original, completo.")
    page.get_by_role("button", name="Solicitar corrección").click()
    see(page.get_by_text("Resolución registrada."))
    case = customer.get(f"/cases/{snap['case_id']}")
    assert case["workflow_state"] != "HUMAN_REVIEW"
    assert case["next_action"]["type"] == "CORRECT_REQUESTED"
    assert case["next_action"]["targets"] == ["INCOME"]


def test_only_advisor_tokens_enter_and_the_session_lives_in_the_tab(new_page, creds):
    page = new_page()
    login(page, creds["cliente-ana"])
    see(page.get_by_text("Ese token no es de un asesor."))
    login(page, "token-invalido")
    see(page.get_by_text("Token inválido."))
    login(page, creds["asesor-1"])
    see(page.get_by_role("heading", name="Bandeja de revisiones"))
    page.reload()  # la sesión sigue en la pestaña (sessionStorage)
    see(page.get_by_role("heading", name="Bandeja de revisiones"))
    page.get_by_role("button", name="Salir").click()
    see(page.get_by_label("Token"))
    assert page.get_by_role("heading", name="Bandeja de revisiones").is_hidden()
