"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# anchored to the service root so tests/tools never pick up a repo-root .env
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=_ENV_FILE, extra="ignore"
    )

    service_name: str = "rag-service"
    port: int = 8001
    log_level: str = "INFO"
    environment: str = "dev"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # ---------------------------------------------------------------- store
    # "chroma"  = persistent ChromaDB (production)
    # "memory"  = in-process keyword store (dev/CI)
    store_backend: Literal["chroma", "memory"] = "memory"
    chroma_path: str = "./chroma_db"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ---------------------------------------------------------------- summarizer
    # "bedrock"    = AWS Bedrock (primary cloud)
    # "litellm"    = multi-provider router (Groq → Gemini → OpenAI → Ollama)
    # "ollama"     = local Ollama directly (legacy single-provider)
    # "extractive" = deterministic extractive fallback (dev/CI, no LLM calls)
    summarizer_backend: Literal["bedrock", "litellm", "ollama", "extractive"] = "extractive"

    # bedrock
    bedrock_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    aws_region: str = "us-east-1"

    # Ollama (legacy / last-resort)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # ---------------------------------------------------------------- LLM router
    # Comma-separated provider priority; used when summarizer_backend == "litellm".
    # Ignored when environment == "aws" (Bedrock used exclusively).
    llm_provider_chain: str = "groq,gemini,openai,ollama"

    # Groq
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"

    # Google Gemini
    google_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini/gemini-2.0-flash"

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"

    # GitHub Models (OpenAI-compatible endpoint, PAT auth)
    github_api_key: SecretStr = SecretStr("")
    github_model: str = "gpt-4o-mini"
    github_base_url: str = "https://models.inference.ai.azure.com"


settings = Settings()
