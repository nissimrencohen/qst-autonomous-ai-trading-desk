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
        env_prefix="AGENTIC_", env_file=_ENV_FILE, extra="ignore"
    )

    service_name: str = "agentic-engine"
    port: int = 8003
    log_level: str = "INFO"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # Deployment environment.
    # "aws"  → all LLM calls go exclusively to AWS Bedrock (no fallback chain).
    # anything else → multi-provider fallback chain defined by llm_provider_chain.
    environment: str = "dev"

    # ---------------------------------------------------------------- engines
    # "crew"          = CrewAI crew via the LLM router (production)
    # "deterministic" = rule-based fallback (dev/CI/degraded mode)
    engine_backend: Literal["crew", "deterministic"] = "deterministic"
    max_runs: int = 200  # run traces kept in-memory for /runs/{id}

    # ---------------------------------------------------------------- bedrock
    bedrock_model_id: str = "anthropic.claude-sonnet-4-6"
    aws_region: str = "us-east-1"

    # ---------------------------------------------------------------- LLM router
    # Comma-separated provider priority (left = first tried).
    # Ignored when environment == "aws" (Bedrock used exclusively).
    llm_provider_chain: str = "groq,gemini,openai,ollama"

    # Groq — fastest free-tier inference
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

    # Local Ollama — last resort in fallback chain
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"

    # ---------------------------------------------------------------- Helicone (proxy-based caching + cost analytics)
    # Set AGENTIC_HELICONE_API_KEY to route Groq/OpenAI calls through the
    # Helicone proxy.  Leave empty to call providers directly (default).
    helicone_api_key: SecretStr = SecretStr("")
    # When True (default), sends Helicone-Cache-Enabled: true on every proxied
    # request so repeated identical prompts are served from Helicone's cache.
    helicone_cache_enabled: bool = True

    # ---------------------------------------------------------------- web search
    web_search_enabled: bool = True
    # Tavily gives higher-quality financial results; falls back to DuckDuckGo if empty.
    tavily_api_key: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- memory
    # "sqlite"   = local SQLite file (default)
    # "memory"   = in-process dict, no persistence (CI/tests)
    # "dynamodb" = AWS DynamoDB (production cloud)
    memory_backend: Literal["sqlite", "memory", "dynamodb"] = "sqlite"
    memory_db_path: str = "./data/agent_memory.db"
    dynamodb_table: str = "trading-desk-memory"
    dynamodb_region: str = "us-east-1"


settings = Settings()
