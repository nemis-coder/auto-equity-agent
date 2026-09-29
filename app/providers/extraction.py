"""Extracción documental: puerto, fake por hash y adaptador LLM (TDD §5.1, §5.3, §6.6).

El resultado del proveedor es un dato no confiable. El servidor lo sanea: conserva solo los
campos críticos del tipo declarado, valida página y bbox, y añade él mismo los metadatos
(documento, hash, proveedor, modelo). Nunca se envían al modelo los valores esperados.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from app.domain.documents import CRITICAL_FIELDS, DOCUMENT_KINDS
from app.providers.base import ProviderError
from app.providers.llm import png_blocks, structured_call

log = logging.getLogger(__name__)

MANIFEST = Path(__file__).resolve().parents[2] / "fixtures" / "documents" / "manifest.json"
PROMPT = Path(__file__).resolve().parent / "prompts" / "extractor.txt"
SCHEMA_VERSION = "1"
PROMPT_VERSION = "extractor-v3"
ALL_FIELDS = sorted({f for fields in CRITICAL_FIELDS.values() for f in fields})


class ExtractionUnavailable(ProviderError):
    """Modelo o proveedor no disponible: el caso se pausa, no se usa un fixture en silencio."""

    retryable = True

    def __init__(self, code: str, *, usage: dict[str, int] | None = None):
        super().__init__(code)
        self.usage = usage


@dataclass(frozen=True)
class ExtractionResult:
    extraction: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, int] = field(default_factory=dict)


class ExtractionProvider(Protocol):
    name: str

    async def extract(
        self, content: bytes, mime_type: str, declared_kind: str, page_count: int
    ) -> ExtractionResult: ...


def sanitize_extraction(raw: Any, declared_kind: str, page_count: int) -> dict[str, Any]:
    """Normaliza una extracción no confiable al esquema interno."""
    raw = raw if isinstance(raw, dict) else {}
    detected = raw.get("detected_type")
    fields_in = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    fields: dict[str, Any] = {}
    for name in CRITICAL_FIELDS.get(declared_kind, ()):
        spec = fields_in.get(name) if isinstance(fields_in.get(name), dict) else {}
        value = spec.get("value")
        value = None if value is None else str(value)[:200]
        confidence = spec.get("confidence")
        try:
            confidence = float(Decimal(str(confidence))) if confidence is not None else None
        except (InvalidOperation, ValueError):
            confidence = None
        if confidence is not None and not 0 <= confidence <= 1:
            confidence = None
        page, bbox = spec.get("page"), spec.get("bbox")
        valid_location = (
            isinstance(page, int)
            and 1 <= page <= page_count
            and isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(v, int | float) and 0 <= v <= 1 for v in bbox)
            and bbox[2] > bbox[0]
            and bbox[3] > bbox[1]
        )
        if value is not None and not valid_location:
            confidence = None  # evidencia sin ubicación válida: no cuenta como confiable
        fields[name] = {
            "value": value,
            "confidence": confidence,
            "page": page if valid_location else None,
            "bbox": bbox if valid_location else None,
            "evidence_text": str(spec.get("evidence_text") or "")[:200],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "detected_type": detected if detected in DOCUMENT_KINDS else "UNSUPPORTED",
        "legible": raw.get("legible") is True,
        "fields": fields,
        "warnings": [str(w)[:64] for w in (raw.get("warnings") or [])][:10],
    }


class FakeExtractionProvider:
    """Resuelve por SHA-256 del archivo (no por nombre). Solo para APP_MODE demo/test."""

    name = "fake"

    def __init__(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._by_hash = {d["sha256"]: d["extraction"] for d in manifest["documents"]}

    async def extract(
        self, content: bytes, mime_type: str, declared_kind: str, page_count: int
    ) -> ExtractionResult:
        import hashlib

        raw = self._by_hash.get(hashlib.sha256(content).hexdigest())
        if raw is None:
            raw = {"detected_type": "UNSUPPORTED", "legible": False, "fields": {},
                   "warnings": ["UNKNOWN_FIXTURE"]}  # fmt: skip
        return ExtractionResult(sanitize_extraction(raw, declared_kind, page_count), "fake",
                                "fixture-v1")  # fmt: skip


def render_pages(content: bytes, mime_type: str, max_pages: int) -> list[bytes]:
    """PDF → PNG por página con pdfium; imágenes → PNG re-codificado."""
    from PIL import Image

    if mime_type == "application/pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
        try:
            pages = []
            for index in range(min(len(pdf), max_pages)):
                bitmap = pdf[index].render(scale=2)
                buffer = io.BytesIO()
                bitmap.to_pil().save(buffer, "PNG")
                pages.append(buffer.getvalue())
            return pages
        finally:
            pdf.close()
    with Image.open(io.BytesIO(content)) as img:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "PNG")
        return [buffer.getvalue()]


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Campo explícitamente desconocido en el esquema documental."""
    return {"anyOf": [schema, {"type": "null"}]}


def _json_schema(declared_kind: str | None = None) -> dict[str, Any]:
    # Solo los campos del tipo declarado: menos tokens, sin proporcionar valores esperados.
    names = list(CRITICAL_FIELDS[declared_kind]) if declared_kind else ALL_FIELDS
    field_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "confidence", "page", "bbox", "evidence_text"],
        "properties": {
            "value": _nullable({"type": "string"}),
            "confidence": _nullable({"type": "number"}),
            "page": _nullable({"type": "integer"}),
            "bbox": _nullable({"type": "array", "items": {"type": "number"}}),
            "evidence_text": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["detected_type", "legible", "fields", "warnings"],
        "properties": {
            "detected_type": {"type": "string", "enum": [*DOCUMENT_KINDS, "UNSUPPORTED"]},
            "legible": {"type": "boolean"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": names,
                "properties": {name: field_schema for name in names},
            },
        },
    }


class LLMExtractionProvider:
    """Extracción visual con salida estructurada vía un adaptador LangChain.

    No recibe valores esperados del perfil. `name` identifica la ruta en auditoría y trazas.
    """

    def __init__(self, chat_model: Any, name: str, model: str, max_pages: int) -> None:
        self._chat = chat_model
        self.name = name
        self._model = model
        self.model = model
        self._max_pages = max_pages
        self._instructions = PROMPT.read_text(encoding="utf-8")

    async def extract(
        self, content: bytes, mime_type: str, declared_kind: str, page_count: int
    ) -> ExtractionResult:
        pages = render_pages(content, mime_type, self._max_pages)
        message = [{"type": "text", "text": "Extrae el documento según el esquema."},
                   *png_blocks(pages)]  # fmt: skip
        input_tokens = output_tokens = 0
        usage: dict[str, int] = {}
        # Un solo reintento para corregir formato; un rechazo o un segundo fallo pausan (§5.4).
        for _ in range(2):
            try:
                out = await structured_call(
                    self._chat,
                    provider=self.name,
                    system=self._instructions,
                    content=message,
                    schema=_json_schema(declared_kind),
                    name="document_extraction",
                )
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del proveedor pausa
                log.warning("extraction_provider_failed %s", type(exc).__name__)
                raise ExtractionUnavailable(type(exc).__name__, usage=usage or None) from exc
            input_tokens += out.input_tokens
            output_tokens += out.output_tokens
            for key, value in (out.usage or {
                "input": out.input_tokens, "output": out.output_tokens,
            }).items():
                usage[key] = usage.get(key, 0) + value
            if out.refused:
                break
            if out.data is None:
                continue
            return ExtractionResult(
                sanitize_extraction(out.data, declared_kind, page_count),
                self.name,
                self._model,
                input_tokens,
                output_tokens,
                usage,
            )
        raise ExtractionUnavailable("INVALID_OR_REFUSED_OUTPUT", usage=usage or None)
