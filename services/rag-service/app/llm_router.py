"""LLM provider factory — budget-first fallback chain + Helicone proxy.

Budget-first priority (default): Groq → OpenAI → Gemini → Ollama
Configurable via RAG_LLM_PROVIDER_CHAIN (comma-separated).
When RAG_ENVIRONMENT=aws: Bedrock only.
When RAG_FORCE_LOCAL_OLLAMA=true: Ollama only (overrides everything).

Helicone injects Helicone-Property-Provider so you can track provider
spend in the dashboard and see exactly when spilling from free to paid.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

_HELICONE_PROXIES: dict[str, str] = {
    "groq":   "https://groq.helicone.ai/openai/v1",
    "openai": "https://oai.helicone.ai/v1",
    # gemini deliberately NOT proxied: Helicone's Google-AI-Studio gateway is
    # incompatible with litellm's native `gemini/` provider — it raises
    # "Missing target base url". Gemini calls go direct to Google (mirrors the
    # agentic-engine router). Re-adding this line breaks every Gemini RAG query.
}


def provider_chain() -> list[tuple[str, dict[str, Any]]]:
    """Return [(model_str, litellm_kwargs), ...] in configured priority order.

    FORCE_LOCAL_OLLAMA=true collapses the chain to Ollama only.
    Callers iterate and fall back to the next entry on exception.
    """
    if settings.force_local_ollama:
        log.info("FORCE_LOCAL_OLLAMA — routing all RAG calls to Ollama")
        entry = _build_entry("ollama")
        return [entry] if entry else []

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
        kwargs: dict[str, Any] = {"api_key": key}
        kwargs.update(_helicone_overrides("groq"))
        return (f"groq/{settings.groq_model}", kwargs)

    if name == "openai":
        key = settings.openai_api_key.get_secret_value()
        if not key:
            log.debug("Skipping openai — no RAG_OPENAI_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("openai"))
        return (settings.openai_model, kwargs)

    if name == "gemini":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini — no RAG_GOOGLE_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("gemini"))
        return (settings.gemini_model, kwargs)

    if name == "gemini_flash":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini_flash — no RAG_GOOGLE_API_KEY")
            return None
        # Direct (no Helicone proxy — google-ai-studio gateway incompatibility).
        return (settings.gemini_flash_model, {"api_key": key})

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
            f"openai/{settings.ollama_model}",
            {"api_key": "ollama", "api_base": f"{settings.ollama_url.rstrip('/')}/v1"},
        )

    log.warning("Unknown provider in RAG_LLM_PROVIDER_CHAIN: %r", name)
    return None


def _helicone_overrides(provider_name: str) -> dict[str, Any]:
    """Return api_base + extra_headers to route through Helicone.

    Injects Helicone-Property-Provider for per-provider cost tracking in
    the Helicone dashboard — shows exactly when spend spills into paid tiers.
    """
    key = settings.helicone_api_key.get_secret_value()
    if not key:
        return {}

    proxy_base = _HELICONE_PROXIES.get(provider_name)
    if not proxy_base:
        log.debug("Helicone: no proxy for provider=%s, calling directly", provider_name)
        return {}

    headers: dict[str, str] = {
        "Helicone-Auth": f"Bearer {key}",
        "Helicone-Property-Provider": provider_name,
    }
    if settings.helicone_cache_enabled:
        headers["Helicone-Cache-Enabled"] = "true"

    log.debug("Helicone: routing %s → %s (cache=%s)",
              provider_name, proxy_base, settings.helicone_cache_enabled)
    return {
        "api_base": proxy_base,
        "extra_headers": headers,
    }
