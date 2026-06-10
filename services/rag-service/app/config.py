"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=".env", extra="ignore"
    )

    service_name: str = "rag-service"
    port: int = 8001
    log_level: str = "INFO"
    environment: str = "dev"


settings = Settings()
