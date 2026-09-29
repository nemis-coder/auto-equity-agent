"""Configuración compartida por API y agente; sin credenciales de negocio."""

from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD por millón de tokens. Tarifas explícitas, no una tabla de precios implícita."""

    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)
    cache_read: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_creation: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    trace_dir: Path = Path("/data/traces")
    langfuse_enabled: bool = False
    langfuse_base_url: str | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_environment: str = "local"
    # Clave exacta "proveedor:modelo". Sin coincidencia, costo desconocido (no cero).
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_telemetry(self):
        if self.langfuse_enabled:
            missing = [
                name for name, value in (
                    ("LANGFUSE_BASE_URL", self.langfuse_base_url),
                    ("LANGFUSE_PUBLIC_KEY", self.langfuse_public_key),
                    ("LANGFUSE_SECRET_KEY", self.langfuse_secret_key),
                )
                if not value or (isinstance(value, SecretStr) and not value.get_secret_value())
            ]
            if missing:
                raise ValueError("LANGFUSE_ENABLED=true requiere " + ", ".join(missing)
                                 + ". Para el perfil evaluador usa LANGFUSE_ENABLED=false.")
        return self
