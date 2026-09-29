from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from app.observability.settings import TelemetrySettings


class Settings(TelemetrySettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    app_mode: Literal["demo", "test", "real_ai"] = "demo"
    database_url: str

    document_store: Literal["s3"] = "s3"
    s3_endpoint_url: str
    s3_region: str = "us-east-1"
    s3_bucket: str = "auto-equity-documents"
    s3_namespace: str = ""  # prefijo físico por entorno: "" demo, "test/" pruebas, "eval/" evals
    s3_access_key_id: str
    s3_secret_access_key: SecretStr

    policy_version: str = "demo_policy_v1"
    clock_mode: Literal["system", "fixed"] = "system"
    fixed_now: datetime | None = None
    pending_action_ttl_minutes: int = 15
    rate_limit_per_minute: int = 30
    read_rate_limit_per_minute: int = 120
    provider_retry_delays: list[float] = [0.5, 1.0]
    provider_timeout_seconds: float = 5.0
    turn_timeout_seconds: float = 60.0
    # Llamadas del agente al modelo por caso, antes de pasar a un asesor (se reinicia al
    # resolver esa revisión). Un recorrido completo con correcciones usa de 15 a 30.
    max_turns_per_case: int = 60
    # Supuesto S10: presupuesto de demo por caso, compartido por la conversación y la lectura de
    # documentos; los tokens se estiman, no se facturan. La conversación reenvía su historial en
    # cada llamada (4 a 8k tokens), por eso es mayor que el del agente interno original.
    case_token_budget_input: int = 400_000
    case_token_budget_output: int = 40_000
    call_max_input_tokens: int = 16_000
    call_max_output_tokens: int = 1_500
    max_file_bytes: int = 10 * 1024 * 1024
    max_document_pages: int = 3
    max_image_pixels: int = 20_000_000
    # Vacío solo en Anthropic: modelo documental ya evaluado (app/providers/llm.py).
    extraction_model: str | None = None
    # Proveedores reales explícitos; fake es solo para pruebas, nunca un fallback (0043).
    extraction_provider: Literal["fake", "anthropic", "openai"] = "fake"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.app_mode == "real_ai" and self.extraction_provider == "fake":
            raise ValueError("APP_MODE=real_ai no admite el extractor fake.")
        if self.extraction_provider != "fake":
            if missing := self.missing_llm_key(self.extraction_provider):
                raise ValueError(f"El proveedor {self.extraction_provider} requiere {missing}.")
        if self.extraction_model is not None:
            self.extraction_model = self.extraction_model.strip() or None
        if self.extraction_provider == "openai" and not self.extraction_model:
            raise ValueError("OpenAI requiere EXTRACTION_MODEL explícito; evalúa ese modelo.")
        return self

    def missing_llm_key(self, provider: str) -> str | None:
        """Variable de credencial que falta para usar `provider`, o None si está completa."""
        if provider == "fake":
            return None
        keys = {"anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
                "openai": ("OPENAI_API_KEY", self.openai_api_key)}
        if provider not in keys:
            raise ValueError(f"Proveedor no soportado: {provider}")
        name, value = keys[provider]
        return None if value and value.get_secret_value().strip() else name


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
