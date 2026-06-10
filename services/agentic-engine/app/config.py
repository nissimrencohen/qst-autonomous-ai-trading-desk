"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_", env_file=".env", extra="ignore"
    )

    service_name: str = "agentic-engine"
    port: int = 8003
    log_level: str = "INFO"
    environment: str = "dev"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # "crew" = CrewAI on Bedrock (production); "deterministic" = rule-based
    # dev/CI/degraded-mode fallback with the same output contract.
    engine_backend: Literal["crew", "deterministic"] = "deterministic"
    bedrock_model_id: str = "anthropic.claude-sonnet-4-6"
    aws_region: str = "us-east-1"
    max_runs: int = 200  # run traces kept in memory for /runs/{id}


settings = Settings()
