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

    # "torch"     = ChartConditionNet inference (ResNet-50)
    # "heuristic" = deterministic pixel analysis (dev/CI/no-GPU)
    # "llm"       = multimodal LLM: gpt-4o-mini → escalate to gemini-2.5-flash
    model_backend: Literal["torch", "heuristic", "llm"] = "heuristic"
    model_path: str = ""  # path to ChartConditionNet state_dict (torch backend)
    max_image_bytes: int = 5 * 1024 * 1024
    bullish_threshold: float = 0.15  # |score| above this leaves "neutral"

    # ---------------------------------------------------------------- LLM vision (model_backend=llm)
    # Primary: gpt-4o-mini for fast, cheap chart interpretation
    openai_api_key: str = ""
    llm_vision_primary_model: str = "gpt-4o-mini"
    # Escalation: gemini-2.5-flash when primary returns low confidence
    google_api_key: str = ""
    llm_vision_escalation_model: str = "gemini/gemini-2.5-flash"
    # Confidence below this threshold triggers escalation to the heavier model
    llm_vision_escalation_threshold: float = 0.60


settings = Settings()
