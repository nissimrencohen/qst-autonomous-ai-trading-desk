"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUARDRAILS_", env_file=".env", extra="ignore"
    )

    service_name: str = "guardrails-service"
    port: int = 8004
    log_level: str = "INFO"
    environment: str = "dev"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # "nemo" = deterministic rules + NeMo LLM self-check rails (production);
    # "rules" = deterministic rules only (dev/CI/degraded mode).
    backend: Literal["nemo", "rules"] = "rules"


settings = Settings()
