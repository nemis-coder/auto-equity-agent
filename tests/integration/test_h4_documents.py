from __future__ import annotations

import hashlib
import io
import uuid
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from app.api.main import create_app
from app.domain.clock import FakeClock
from app.storage.s3 import S3DocumentStore
from tests.integration.test_h2_cases import FIXED, PROFILE, Api, h, pick

DOCS = Path(__file__).resolve().parents[2] / "fixtures" / "documents"
VEHICLE = {
    "owned_by_customer": True,
    "blocking_debt": False,
    "has_second_key": True,
    "vehicle_ref": "ABC-123-XYZ",
    "make": "Nissan",
    "model": "Versa",
    "year": 2020,
}
HAPPY = {
    "IDENTITY": "identity_ana.png",
    "PAYSLIP": "payslip_ana.pdf",
    "VEHICLE_OWNERSHIP": "ownership_ana.pdf",
}


def _settings(settings, **extra):
    return settings.model_copy(
        update={
            "rate_limit_per_minute": 10_000,
            "read_rate_limit_per_minute": 10_000,
            "provider_retry_delays": [0.0, 0.0],
            **extra,
        }
    )


@pytest.fixture(scope="module")
def client(settings, demo_credentials):
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED))) as c:
        yield c


@pytest.fixture
def ana(client, demo_credentials):
    return Api(client, demo_credentials["cliente-ana"])


def upload(api: Api, snap: dict, kind: str, filename: str | None = None, *, content=None,
           supersedes=None, key=None):  # fmt: skip
    data = content if content is not None else (DOCS / filename).read_bytes()
    headers = h(api.t, version=snap["case_version"])
    if key:
        headers["Idempotency-Key"] = key
    form = {"declared_type": kind}
    if supersedes:
        form["supersedes_id"] = supersedes
    return api.c.post(
        f"/cases/{snap['case_id']}/documents",
        headers=headers,
        files={"file": (filename or "archivo.bin", data, "application/octet-stream")},
        data=form,
    )


def to_documents(api: Api) -> dict:
    snap = api.create()
    snap = api.declare(snap, "vehicle", VEHICLE)
    snap = api.declare(snap, "profile", PROFILE)
    offer = pick(snap)
    r = api.c.post(
        f"/cases/{snap['case_id']}/selections",
        headers=h(api.t, version=snap["case_version"]),
        json={"offer_id": offer["offer_id"], "displayed_offer_hash": offer["display_hash"]},
    )
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["workflow_state"] == "DOCUMENT_COLLECTION"
    return snap


def upload_all(api: Api, snap: dict, files: dict[str, str]) -> dict:
    for kind, name in files.items():
        r = upload(api, snap, kind, name)
        assert r.status_code == 201, r.text
        snap = r.json()
    return snap


def failed(snap: dict) -> dict[str, str]:
    return {v["rule_id"]: v["reason_code"] for v in snap["validations"] if v["status"] != "PASS"}


def test_d01_documents_valid_pass_rules_and_final_gate(ana, client, demo_credentials):
    snap = to_documents(ana)
    assert snap["next_action"] == {
        "type": "UPLOAD_DOCUMENTS",
        "missing": ["IDENTITY", "INCOME", "VEHICLE_OWNERSHIP"],
    }
    snap = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"]).json()
    assert snap["workflow_state"] == "DOCUMENT_COLLECTION"
    assert snap["documents"][0]["status"] == "EXTRACTED"
    assert snap["next_action"]["missing"] == ["INCOME", "VEHICLE_OWNERSHIP"]
    snap = upload_all(ana, snap, {k: v for k, v in HAPPY.items() if k != "IDENTITY"})

    # Desde H6 el gate final corre en el mismo avance (decisión 0024).
    assert snap["workflow_state"] == "READY_FOR_FINANCIAL"
    assert len(snap["validations"]) == 11
    assert failed(snap) == {}
    assert snap["next_action"] == {"type": "NONE"}

    doc = next(d for d in snap["documents"] if d["kind"] == "PAYSLIP")
    url = f"/cases/{snap['case_id']}/documents/{doc['document_id']}/content"
    r = client.get(url, headers=h(ana.t, key=False))
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    expected = hashlib.sha256((DOCS / HAPPY["PAYSLIP"]).read_bytes()).hexdigest()
    assert hashlib.sha256(r.content).hexdigest() == expected
    other = client.get(url, headers=h(demo_credentials["cliente-beto"], key=False))
    assert other.status_code == 404 and not other.content.startswith(b"%PDF")
    advisor = client.get(url, headers=h(demo_credentials["asesor-1"], key=False))
    assert advisor.status_code == 200


def test_d05_then_d14_income_mismatch_is_corrected_with_new_evidence(ana):
    snap = to_documents(ana)
    snap = upload_all(ana, snap, {**HAPPY, "PAYSLIP": "payslip_ana_low.pdf"})
    assert snap["workflow_state"] == "NEEDS_CORRECTION"
    assert failed(snap) == {"INCOME_MATCH": "INCOME_MISMATCH"}
    assert snap["correction_rounds"] == 1
    reasons = snap["next_action"]["reasons"]
    assert reasons[0]["rule_id"] == "INCOME_MATCH" and reasons[0]["refs"][0]["slot"] == "INCOME"

    current = next(d for d in snap["documents"] if d["slot"] == "INCOME")
    r = upload(ana, snap, "PAYSLIP", "payslip_ana.pdf", supersedes=current["document_id"])
    assert r.status_code == 201, r.text
    fixed = r.json()
    assert fixed["workflow_state"] == "READY_FOR_FINANCIAL"  # D14: READY con la evidencia nueva
    assert failed(fixed) == {}
    income = next(d for d in fixed["documents"] if d["slot"] == "INCOME")
    assert income["revision"] == 2 and income["document_id"] != current["document_id"]


def test_d15_two_failed_rounds_escalate_and_identical_reupload_does_not_count(ana):
    snap = to_documents(ana)
    snap = upload_all(ana, snap, {**HAPPY, "PAYSLIP": "payslip_ana_low.pdf"})
    assert snap["correction_rounds"] == 1
    same = upload(ana, snap, "PAYSLIP", "payslip_ana_low.pdf").json()
    assert same["workflow_state"] == "NEEDS_CORRECTION"
    assert same["correction_rounds"] == 1  # mismo SHA-256: no es evidencia nueva
    second = upload(ana, same, "PAYSLIP", "payslip_ana_lowconf.pdf").json()
    assert second["correction_rounds"] == 2
    assert second["workflow_state"] == "HUMAN_REVIEW"
    assert second["next_action"] == {"type": "WAIT_REVIEW"}


@pytest.mark.parametrize(
    ("kind", "filename", "rule", "reason"),
    [
        ("IDENTITY", "identity_other_name.png", "NAME_MATCH", "NAME_MISMATCH"),
        ("IDENTITY", "identity_other_address.png", "ADDRESS_MATCH", "ADDRESS_MISMATCH"),
        ("IDENTITY", "identity_expired.png", "DATES", "IDENTITY_EXPIRED"),
        ("PAYSLIP", "payslip_ana_old.pdf", "DATES", "INCOME_DOCUMENT_TOO_OLD"),
        ("IDENTITY", "identity_blurry.png", "EXTRACTION_QUALITY", "ILLEGIBLE"),
        ("PAYSLIP", "payslip_ana_lowconf.pdf", "EXTRACTION_QUALITY", "LOW_CONFIDENCE"),
        ("PAYSLIP", "payslip_ana_usd.pdf", "INCOME_CURRENCY", "INCOME_CURRENCY_MISMATCH"),
        ("PAYSLIP", "payslip_ana_gross.pdf", "INCOME_BASIS", "INCOME_NOT_NET"),
        (
            "INCOME_STATEMENT",
            "income_statement_ana.pdf",
            "EMPLOYMENT_MATCH",
            "INCOME_DOCUMENT_TYPE_MISMATCH",
        ),
        (
            "VEHICLE_OWNERSHIP",
            "ownership_other_owner.pdf",
            "VEHICLE_MATCH",
            "VEHICLE_OWNER_MISMATCH",
        ),
    ],
)
def test_negative_documents_never_reach_validation_pass(ana, kind, filename, rule, reason):
    files = {k: v for k, v in HAPPY.items() if not (k == "PAYSLIP" and kind == "INCOME_STATEMENT")}
    files[kind] = filename
    snap = upload_all(ana, to_documents(ana), files)
    assert snap["workflow_state"] == "NEEDS_CORRECTION"
    assert failed(snap).get(rule) == reason


def test_d22_injected_instruction_has_no_effect(ana):
    snap = upload_all(ana, to_documents(ana), {**HAPPY, "PAYSLIP": "payslip_ana_injection.pdf"})
    # Reglas normales y gate completo: el texto impreso no crea atajos ni cambia el dictamen.
    assert snap["workflow_state"] == "READY_FOR_FINANCIAL"
    assert failed(snap) == {}


def test_declared_type_different_from_content_fails_required_documents(ana):
    snap = upload_all(ana, to_documents(ana), {**HAPPY, "IDENTITY": "payslip_ana.pdf"})
    assert snap["workflow_state"] == "NEEDS_CORRECTION"
    assert failed(snap) == {"DOC_REQUIRED": "DOCUMENT_TYPE_MISMATCH"}


def test_unknown_file_is_never_invented_by_fake_extractor(ana):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, "PNG")
    snap = to_documents(ana)
    for kind, name in HAPPY.items():
        content = buffer.getvalue() if kind == "IDENTITY" else None
        snap = upload(ana, snap, kind, name, content=content).json()
    assert snap["workflow_state"] == "NEEDS_CORRECTION"
    assert "DOC_REQUIRED" in failed(snap) or "EXTRACTION_QUALITY" in failed(snap)


def test_upload_requires_selected_offer(ana):
    snap = ana.create()
    r = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"])
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATE"


def _pdf(pages=1, encrypt=False, javascript=False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if javascript:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            writer.add_js("app.alert('x');")
    if encrypt:
        writer.encrypt("secreto")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (b"hola, no soy un documento", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"", 422, "INVALID_DOCUMENT"),
        (b"%PDF-1.4 roto", 422, "INVALID_DOCUMENT"),
        (b"\x89PNG\r\n\x1a\nroto", 422, "INVALID_DOCUMENT"),
        (_pdf(encrypt=True), 422, "INVALID_DOCUMENT"),
        (_pdf(javascript=True), 422, "INVALID_DOCUMENT"),
        (_pdf(pages=4), 422, "INVALID_DOCUMENT"),
    ],
)
def test_upload_rejects_unsafe_or_invalid_files(ana, content, status, code):
    snap = to_documents(ana)
    r = upload(ana, snap, "IDENTITY", content=content)
    assert r.status_code == status and r.json()["code"] == code
    assert ana.get(snap["case_id"]).json()["documents"] == []


def test_size_limit(settings, demo_credentials):
    small = _settings(settings, max_file_bytes=1000)
    with TestClient(create_app(small, clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = to_documents(api)
        r = upload(api, snap, "IDENTITY", HAPPY["IDENTITY"])
    assert r.status_code == 413 and r.json()["code"] == "FILE_TOO_LARGE"


def test_supersedes_must_be_the_current_document(ana):
    snap = to_documents(ana)
    snap = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"]).json()
    r = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"], supersedes=str(uuid.uuid4()))
    assert r.status_code == 409 and r.json()["code"] == "DOCUMENT_NOT_CURRENT"


def test_upload_replay_does_not_duplicate(ana):
    snap = to_documents(ana)
    key = f"doc-{uuid.uuid4()}"
    first = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"], key=key)
    replay = upload(ana, snap, "IDENTITY", HAPPY["IDENTITY"], key=key)
    assert first.status_code == replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"
    assert len(ana.get(snap["case_id"]).json()["documents"]) == 1


def test_storage_down_rejects_without_creating_evidence(settings, demo_credentials):
    broken = S3DocumentStore(
        endpoint_url="http://127.0.0.1:1", region="us-east-1", bucket="x",
        access_key_id="x", secret_access_key="x",
    )  # fmt: skip
    with TestClient(create_app(_settings(settings), clock=FakeClock(FIXED), store=broken)) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        snap = to_documents(api)
        r = upload(api, snap, "IDENTITY", HAPPY["IDENTITY"])
        assert r.status_code == 503 and r.json()["code"] == "STORAGE_UNAVAILABLE"
        assert api.get(snap["case_id"]).json()["documents"] == []


def test_declaration_change_invalidates_document_validation(ana):
    snap = upload_all(ana, to_documents(ana), {**HAPPY, "PAYSLIP": "payslip_ana_low.pdf"})
    assert snap["workflow_state"] == "NEEDS_CORRECTION" and snap["validations"]
    income = {**PROFILE["income"], "amount": "30000.00"}
    changed = ana.declare(snap, "profile", {"income": income})
    assert changed["validations"] == []
    assert changed["selection"] is None  # la oferta elegida dependía del ingreso
    assert changed["workflow_state"] == "SIMULATION"
    assert len(changed["documents"]) == 3  # la evidencia se conserva; se revalida después


class _DownExtractor:
    name = "anthropic"

    async def extract(self, content, mime_type, declared_kind, page_count):
        from app.providers.extraction import ExtractionUnavailable

        raise ExtractionUnavailable("AnthropicInvalidRequestError")


def _extraction_spans(trace_dir: Path) -> list[dict]:
    import json

    lines = [line for p in trace_dir.glob("*.jsonl") for line in p.read_text().splitlines()]
    records = [json.loads(line) for line in lines]
    return [r for r in records if r["name"] == "document.extraction"]


def test_each_extraction_is_traced_with_model_tokens_and_outcome(
    settings, demo_credentials, tmp_path
):
    traced = _settings(settings, trace_dir=str(tmp_path))
    with TestClient(create_app(traced, clock=FakeClock(FIXED))) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        upload_all(api, to_documents(api), HAPPY)
    spans = _extraction_spans(tmp_path)
    assert sorted(s["metadata"]["document_kind"] for s in spans) == [
        "IDENTITY", "PAYSLIP", "VEHICLE_OWNERSHIP"
    ]  # fmt: skip
    for s in spans:
        assert s["kind"] == "generation" and s["level"] == "DEFAULT"
        assert s["metadata"]["outcome"] == "OK" and s["metadata"]["provider"] == "fake"
        assert s["metadata"]["prompt_version"].startswith("extractor-")
        assert set(s["usage"]) == {"input", "output"}
    content = "".join(p.read_text() for p in tmp_path.glob("*.jsonl"))
    assert "Ana Prueba" not in content and "Calle Demo" not in content


def test_unavailable_extraction_is_traced_as_error(settings, demo_credentials, tmp_path):
    traced = _settings(settings, trace_dir=str(tmp_path))
    app = create_app(traced, clock=FakeClock(FIXED), extractor=_DownExtractor())
    with TestClient(app) as c:
        api = Api(c, demo_credentials["cliente-ana"])
        upload(api, to_documents(api), "IDENTITY", HAPPY["IDENTITY"])
    [span] = _extraction_spans(tmp_path)
    assert span["level"] == "ERROR"
    assert span["metadata"]["outcome"] == "MODEL_UNAVAILABLE"
    assert span["metadata"]["error_code"] == "AnthropicInvalidRequestError"
    assert span["usage"] is None
