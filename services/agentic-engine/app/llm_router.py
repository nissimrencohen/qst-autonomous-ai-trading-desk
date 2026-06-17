"""LLM provider factory with fallback chain.

Priority (default): Groq → Gemini → OpenAI → Ollama.
Configurable via AGENTIC_LLM_PROVIDER_CHAIN (comma-separated).
When AGENTIC_ENVIRONMENT=aws the chain is bypassed and Bedrock is used
exclusively — no fallback, no external API keys needed.

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

    Used by LiteLLMSummarizer-style callers that iterate and fall back on exception.
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


def pick_crewai_llm():
    """Return the first available provider as a crewai.LLM object.

    CrewAI constructs the LLM once per crew, so we pick the best available
    provider at engine-init time. Mid-run switching is not supported by
    CrewAI's architecture.
    """
    from crewai import LLM

    if settings.environment.lower() == "aws":
        log.info("LLM router: using Bedrock %s (aws mode)", settings.bedrock_model_id)
        return LLM(
            model=f"bedrock/{settings.bedrock_model_id}",
            temperature=0.2,
        )

    for name in settings.llm_provider_chain.split(","):
        name = name.strip().lower()
        entry = _build_entry(name)
        if entry is None:
            continue
        model, kwargs = entry
        log.info("LLM router: selected provider=%s model=%s", name, model)
        # crewai.LLM accepts api_key and base_url kwargs directly
        crewai_kwargs: dict[str, Any] = {"temperature": 0.2}
        if "api_key" in kwargs:
            crewai_kwargs["api_key"] = kwargs["api_key"]
        if "api_base" in kwargs:
            crewai_kwargs["base_url"] = kwargs["api_base"]
        return LLM(model=model, **crewai_kwargs)

    raise RuntimeError(
        "No LLM provider available — set at least one API key "
        "(AGENTIC_GROQ_API_KEY / AGENTIC_GOOGLE_API_KEY / AGENTIC_OPENAI_API_KEY) "
        "or ensure Ollama is reachable at AGENTIC_OLLAMA_URL."
    )


# ── internal ──────────────────────────────────────────────────────────────────

def _build_entry(name: str) -> tuple[str, dict[str, Any]] | None:
    if name == "groq":
        key = settings.groq_api_key.get_secret_value()
        if not key:
            log.debug("Skipping groq — no AGENTIC_GROQ_API_KEY")
            return None
        return (f"groq/{settings.groq_model}", {"api_key": key})

    if name == "gemini":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini — no AGENTIC_GOOGLE_API_KEY")
            return None
        return (settings.gemini_model, {"api_key": key})

    if name == "openai":
        key = settings.openai_api_key.get_secret_value()
        if not key:
            log.debug("Skipping openai — no AGENTIC_OPENAI_API_KEY")
            return None
        return (settings.openai_model, {"api_key": key})

    if name == "ollama":
        # Ollama needs no key; always included as last resort
        return (
            f"ollama/{settings.ollama_model}",
            {"api_base": f"{settings.ollama_url.rstrip('/')}/v1"},
        )

    log.warning("Unknown provider in AGENTIC_LLM_PROVIDER_CHAIN: %r", name)
    return None
