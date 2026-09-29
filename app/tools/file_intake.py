"""Inspección de archivos subidos (TDD §9.2, supuesto S8).

Se verifican bytes mágicos y decodificación real, no la extensión ni el MIME que declara el
cliente. PDFs cifrados, con adjuntos embebidos o acciones activas se rechazan.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.tools.errors import ActionError

MAGIC = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


@dataclass(frozen=True)
class Inspected:
    mime_type: str
    page_count: int


def _invalid(reason: str) -> ActionError:
    return ActionError(422, "INVALID_DOCUMENT", reason)


def _check_image(content: bytes, max_pixels: int) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as img:
                width, height = img.size
                if width * height > max_pixels:
                    raise _invalid("La imagen supera el máximo de píxeles permitido.")
                img.verify()
            with Image.open(io.BytesIO(content)) as img:
                img.load()
    except ActionError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:  # fmt: skip
        raise _invalid("La imagen no se puede leer.") from exc


def _pdf_has_active_content(reader: PdfReader) -> bool:
    root = reader.trailer.get("/Root")
    root = root.get_object() if root is not None else {}
    if any(key in root for key in ("/OpenAction", "/AA")):
        return True
    names = root.get("/Names")
    names = names.get_object() if names is not None else {}
    if any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
        return True
    for page in reader.pages:
        if "/AA" in page:
            return True
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            if action is not None and action.get_object().get("/S") in ("/JavaScript", "/Launch"):
                return True
    return False


def _check_pdf(content: bytes, max_pages: int) -> int:
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise _invalid("No se aceptan PDFs cifrados.")
        pages = len(reader.pages)
        if _pdf_has_active_content(reader):
            raise _invalid("El PDF contiene acciones o adjuntos no permitidos.")
    except ActionError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise _invalid("El PDF no se puede leer.") from exc
    if pages < 1:
        raise _invalid("El PDF no tiene páginas.")
    if pages > max_pages:
        raise _invalid(f"El documento supera el máximo de {max_pages} páginas.")
    return pages


def inspect_upload(content: bytes, *, max_bytes: int, max_pages: int, max_pixels: int) -> Inspected:
    if not content:
        raise _invalid("El archivo está vacío.")
    if len(content) > max_bytes:
        raise ActionError(413, "FILE_TOO_LARGE", "El archivo supera el tamaño máximo permitido.")
    mime = next((m for magic, m in MAGIC.items() if content.startswith(magic)), None)
    if mime is None:
        raise ActionError(415, "UNSUPPORTED_MEDIA_TYPE", "Solo se aceptan PDF, PNG o JPEG.")
    if mime == "application/pdf":
        return Inspected(mime, _check_pdf(content, max_pages))
    _check_image(content, max_pixels)
    return Inspected(mime, 1)
