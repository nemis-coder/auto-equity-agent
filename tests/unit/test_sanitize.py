from app.observability.sanitize import REDACTED, langfuse_mask, sanitize

PII = {
    "full_name": "Ana Prueba López",
    "address": "Calle Demo 123, Colonia Ejemplo",
    "token": "abcDEF123",
    "document_text": "Neto: $10,000.00 MXN",
}


def test_only_allowlisted_keys_survive():
    out = sanitize({**PII, "stage": "PROFILING", "tool": "query_credit_bureau"})
    assert out == {"stage": "PROFILING", "tool": "query_credit_bureau"}


def test_free_text_in_allowed_key_is_redacted():
    out = sanitize({"reason_code": "Ana Prueba López vive en Calle Demo"})
    assert out["reason_code"] == REDACTED


def test_long_base64_blob_is_redacted():
    blob = "iVBORw0KGgo" + "A" * 500
    assert sanitize({"status": blob})["status"] == REDACTED


def test_nested_structures_are_dropped():
    assert sanitize({"stage": {"full_name": "Ana"}}) == {}


def test_mask_redacts_raw_strings_and_prompts():
    assert langfuse_mask(data="Eres un asistente. Cliente: Ana Prueba López") == REDACTED
    assert langfuse_mask(data=[{"role": "user", "content": "hola"}]) == REDACTED
    assert langfuse_mask(data={"input_tokens": 12, "full_name": "Ana"}) == {"input_tokens": 12}
