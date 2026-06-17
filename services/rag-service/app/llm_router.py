"""LLM provider factory with fallback chain for the RAG service.

Priority (default): Groq → Gemini → OpenAI → Ollama.
Configurable via RAG_LLM_PROVIDER_CHAIN (comma-separated).
When RAG_ENVIRONMENT=aws the chain is bypassed and Bedrock is used exclusively.

Providers without a valid (non-empty) API key are silently skipped.
Ollama requires no key and is always available as last resort.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def provider_chain() -> list[tuple[str, dict[str, Any]]]:
    """Return [(model_str, litellm_kwargs), ...] in configured priority order.

    Callers iterate this list and fall back to the next entry on exception.
    """
    if settings.environment.lower() == "aws":
        return [(f"bedrock/{settings.bedrock_model_id}", {})]

    result: list[tuple[str, dict[str, Any]]] = []
    for name in settings.llm_provider_chain.split(","):
        name = name.strip().lower()
        entry = _build_entry(name)
        if entry is None:
            continue
        result.append(entry)
    return result


# ── internal ──────────────────────────────────────────────────────────────────

def _build_entry(name: str) -> tuple[str, dict[str, Any]] | None:
    if name == "groq":
        key = settings.groq_api_key.get_secret_value()
        if not key:
            log.debug("Skipping groq — no RAG_GROQ_API_KEY")
            return None
        return (f"groq/{settings.groq_model}", {"api_key": key})

    if name == "gemini":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini — no RAG_GOOGLE_API_KEY")
            return None
        return (settings.gemini_model, {"api_key": key})

    if name == "openai":
        key = settings.openai_api_key.get_secret_value()
        if not key:
            log.debug("Skipping openai — no RAG_OPENAI_API_KEY")
            return None
        return (settings.openai_model, {"api_key": key})

    if name == "github":
        key = settings.github_api_key.get_secret_value()
        if not key:
            log.debug("Skipping github — no RAG_GITHUB_API_KEY")
            return None
        return (
            f"openai/{settings.github_model}",
            {"api_key": key, "api_base": settings.github_base_url},
        )

    if name == "ollama":
        return (
            f"ollama/{settings.ollama_model}",
            {"api_base": f"{settings.ollama_url.rstrip('/')}/v1"},
        )

    log.warning("Unknown provider in RAG_LLM_PROVIDER_CHAIN: %r", name)
    return None
