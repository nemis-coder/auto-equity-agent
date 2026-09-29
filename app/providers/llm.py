"""Adaptadores LangChain para Anthropic y OpenAI (0043), sin gateway ni fallbacks.

Chat y extracción comparten la construcción del transporte, no prompts ni reglas de negocio.
El extractor sigue saneando los datos no confiables y el dominio conserva la validación.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.observability.costs import token_usage

DEFAULT_MODEL = "claude-opus-5"
# Conserva el timeout y el transporte ya probados con el esquema documental (0033–0034).
EXTRACTION_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class StructuredOutput:
    data: dict[str, Any] | None
    refused: bool
    input_tokens: int
    output_tokens: int
    usage: dict[str, int] = field(default_factory=dict)


def build_chat_model(
    provider: str,
    model: str,
    *,
    max_output_tokens: int,
    api_key: str,
    timeout: float = EXTRACTION_TIMEOUT_SECONDS,
    max_retries: int = 0,
) -> BaseChatModel:
    """Selección explícita al arrancar; la clave nunca decide la ruta.

    El extractor controla su reintento de formato. El chat conserva su único reintento de
    transporte. No hay selección automática de modelos ni cambio al otro proveedor.
    """
    common = dict(model=model, api_key=api_key, max_tokens=max_output_tokens,
                  timeout=timeout, max_retries=max_retries)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(**common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            **common,
            base_url="https://api.openai.com/v1",
            use_responses_api=True,
            output_version="responses/v1",
            store=False,
            use_previous_response_id=False,  # LangGraph conserva el historial, no OpenAI
            include=["reasoning.encrypted_content"],
            http_socket_options=(),  # conserva HTTPS_PROXY en redes corporativas
        )
    raise ValueError(f"Proveedor no soportado: {provider}")


def png_blocks(pages: list[bytes]) -> list[dict[str, Any]]:
    return [
        {"type": "image", "base64": base64.b64encode(page).decode(), "mime_type": "image/png"}
        for page in pages
    ]


def is_refusal(message: Any) -> bool:
    """Normaliza el rechazo sin reenviar texto del proveedor al cliente ni reintentarlo."""
    metadata = getattr(message, "response_metadata", None) or {}
    blocks = getattr(message, "content", None)
    return bool(
        metadata.get("stop_reason") == "refusal"
        or (getattr(message, "additional_kwargs", None) or {}).get("refusal")
        or (isinstance(blocks, list) and any(
            isinstance(b, dict) and b.get("type") == "refusal" for b in blocks
        ))
    )


def is_incomplete(message: Any) -> bool:
    metadata = getattr(message, "response_metadata", None) or {}
    return metadata.get("status") in {"incomplete", "failed"} or (
        metadata.get("stop_reason") == "max_tokens"
    )


async def structured_call(
    model: BaseChatModel,
    *,
    provider: str,
    system: str,
    content: str | list[dict[str, Any]],
    schema: dict[str, Any],
    name: str,
) -> StructuredOutput:
    schema = {"title": name, **schema}
    if provider == "openai":
        runnable = model.with_structured_output(
            schema, method="json_schema", strict=True, include_raw=True
        )
    elif provider == "anthropic":
        # El esquema documental excede el límite de uniones del modo estricto (0033).
        runnable = model.with_structured_output(
            schema, method="function_calling", include_raw=True
        )
    else:
        raise ValueError(f"Proveedor no soportado: {provider}")
    result = await runnable.ainvoke([SystemMessage(system), HumanMessage(content=content)])
    raw, parsed, error = result.get("raw"), result.get("parsed"), result.get("parsing_error")
    usage = getattr(raw, "usage_metadata", None) or {}
    tokens = (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    if is_refusal(raw):
        return StructuredOutput(None, True, *tokens, token_usage(usage))
    # Incluso un JSON válido no cuenta si el proveedor informa una respuesta incompleta.
    if is_incomplete(raw):
        return StructuredOutput(None, False, *tokens, token_usage(usage))
    if error is not None or not isinstance(parsed, dict):
        return StructuredOutput(None, False, *tokens, token_usage(usage))
    return StructuredOutput(parsed, False, *tokens, token_usage(usage))
