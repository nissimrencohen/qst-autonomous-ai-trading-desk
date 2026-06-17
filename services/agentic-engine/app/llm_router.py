"""LLM provider factory with fallback chain and optional Helicone proxy.

Priority (default): Groq → Gemini → OpenAI → Ollama.
Configurable via AGENTIC_LLM_PROVIDER_CHAIN (comma-separated).
When AGENTIC_ENVIRONMENT=aws the chain is bypassed and Bedrock is used
exclusively — no fallback, no external API keys needed.

Providers without a valid (non-empty) API key are silently skipped.
Ollama requires no key and is always available as last resort.

Helicone proxy:
  Set AGENTIC_HELICONE_API_KEY to route Groq and OpenAI calls through the
  Helicone proxy (https://helicone.ai).  This enables:
    - Prompt/completion logging and cost analytics in the Helicone dashboard
    - Semantic caching: identical prompts are served from cache (controlled by
      AGENTIC_HELICONE_CACHE_ENABLED, default true)
  Providers without a Helicone proxy endpoint (github, ollama) are called
  directly and are unaffected when Helicone is enabled.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# Helicone proxy base URLs per provider.
# github → Azure endpoint has no Helicone proxy; ollama is local — both skipped.
_HELICONE_PROXIES: dict[str, str] = {
    "groq":   "https://groq.helicone.ai/openai/v1",
    "openai": "https://oai.helicone.ai/v1",
    "gemini": "https://gateway.helicone.ai/api/providers/google-ai-studio",
}


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

    When Helicone is configured, extra_headers are injected via
    additional_params so they flow through CrewAI → litellm → HTTP request.
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
        helicone_active = "extra_headers" in kwargs
        log.info(
            "LLM router: selected provider=%s model=%s helicone=%s",
            name, model, helicone_active,
        )

        crewai_kwargs: dict[str, Any] = {"temperature": 0.2}
        if "api_key" in kwargs:
            crewai_kwargs["api_key"] = kwargs["api_key"]
        # api_base from _build_entry may already be the Helicone proxy URL
        if "api_base" in kwargs:
            crewai_kwargs["base_url"] = kwargs["api_base"]
        # extra_headers must travel via additional_params so litellm receives them
        if "extra_headers" in kwargs:
            crewai_kwargs["additional_params"] = {"extra_headers": kwargs["extra_headers"]}
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
        kwargs: dict[str, Any] = {"api_key": key}
        kwargs.update(_helicone_overrides("groq"))
        return (f"groq/{settings.groq_model}", kwargs)

    if name == "gemini":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini — no AGENTIC_GOOGLE_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("gemini"))
        return (settings.gemini_model, kwargs)

    if name == "openai":
        key = settings.openai_api_key.get_secret_value()
        if not key:
            log.debug("Skipping openai — no AGENTIC_OPENAI_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("openai"))
        return (settings.openai_model, kwargs)

    if name == "github":
        key = settings.github_api_key.get_secret_value()
        if not key:
            log.debug("Skipping github — no AGENTIC_GITHUB_API_KEY")
            return None
        # GitHub Models uses Azure endpoint — no Helicone proxy, called directly
        return (
            f"openai/{settings.github_model}",
            {"api_key": key, "api_base": settings.github_base_url},
        )

    if name == "ollama":
        # Ollama is local — no key, no Helicone proxy
        return (
            f"ollama/{settings.ollama_model}",
            {"api_base": f"{settings.ollama_url.rstrip('/')}/v1"},
        )

    log.warning("Unknown provider in AGENTIC_LLM_PROVIDER_CHAIN: %r", name)
    return None


def _helicone_overrides(provider_name: str) -> dict[str, Any]:
    """Return api_base + extra_headers overrides to route through Helicone.

    Returns an empty dict when:
      - AGENTIC_HELICONE_API_KEY is not set (Helicone disabled)
      - The provider has no Helicone proxy endpoint (github, ollama)
    """
    key = settings.helicone_api_key.get_secret_value()
    if not key:
        return {}

    proxy_base = _HELICONE_PROXIES.get(provider_name)
    if not proxy_base:
        log.debug("Helicone: no proxy for provider=%s, calling directly", provider_name)
        return {}

    headers: dict[str, str] = {"Helicone-Auth": f"Bearer {key}"}
    if settings.helicone_cache_enabled:
        headers["Helicone-Cache-Enabled"] = "true"

    log.debug("Helicone: routing %s through %s (cache=%s)",
              provider_name, proxy_base, settings.helicone_cache_enabled)
    return {
        "api_base": proxy_base,
        "extra_headers": headers,
    }
