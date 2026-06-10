"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=".env", extra="ignore"
    )

    service_name: str = "rag-service"
    port: int = 8001
    log_level: str = "INFO"
    environment: str = "dev"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # Vector store: "chroma" (production) | "memory" (dev/CI fallback)
    store_backend: Literal["chroma", "memory"] = "memory"
    chroma_path: str = "./chroma_db"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Summarizer: "bedrock" (primary) | "ollama" (local) | "extractive" (dev/CI)
    summarizer_backend: Literal["bedrock", "ollama", "extractive"] = "extractive"
    bedrock_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    aws_region: str = "us-east-1"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"


settings = Settings()
