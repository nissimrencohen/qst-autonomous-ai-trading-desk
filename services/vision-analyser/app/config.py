"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# anchored to the service root so tests/tools never pick up a repo-root .env
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISION_", env_file=_ENV_FILE, extra="ignore"
    )

    service_name: str = "vision-analyser"
    port: int = 8002
    log_level: str = "INFO"
    environment: str = "dev"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # "torch" = ChartConditionNet inference; "heuristic" = deterministic
    # dev/CI fallback that needs no ML stack or weights.
    model_backend: Literal["torch", "heuristic"] = "heuristic"
    model_path: str = ""  # path to ChartConditionNet state_dict (torch backend)
    max_image_bytes: int = 5 * 1024 * 1024
    bullish_threshold: float = 0.15  # |score| above this leaves "neutral"


settings = Settings()
