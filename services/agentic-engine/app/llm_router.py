"""LLM provider factory — budget-first fallback chain + Helicone proxy.

Budget-first priority (default): Groq → OpenAI → Gemini → Ollama
  Tier 1 (Groq / Llama-3.3-70b)  — free-tier, fastest inference
  Tier 2 (OpenAI / gpt-4o-mini)  — low-cost paid fallback
  Tier 3 (Gemini / 2.0-flash)    — activates only on Tier 1+2 429/error
  Tier 4 (Ollama)                 — offline / air-gapped / zero-cost last resort

Configurable via AGENTIC_LLM_PROVIDER_CHAIN (comma-separated).
When AGENTIC_ENVIRONMENT=aws: Bedrock only, no chain.
When AGENTIC_FORCE_LOCAL_OLLAMA=true: Ollama only (overrides everything).

Helicone proxy (optional):
  Set AGENTIC_HELICONE_API_KEY to route Groq/OpenAI/Gemini through Helicone
  for logging, cost analytics, and semantic caching.
  Every request is tagged with Helicone-Property-Provider so you can see
  exactly when spend spills from free-tier into paid Google credits.

Resilient routing:
  pick_crewai_llm() attaches _attach_resilient_call() to every returned LLM
  instance. This handles two concerns at the call site (not globally):
    1. cache_breakpoint stripping — Anthropic-only key injected by CrewAI ≥0.80;
       Groq/OpenAI/Gemini reject it at the message level.
    2. 429/5xx transparent fallback — on rate-limit or server error the patch
       silently swaps provider attrs, retries, then restores. CrewAI never sees
       the error; the fallback chain mirrors provider_chain() order.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import litellm

from app.config import settings

log = logging.getLogger(__name__)

# Helicone proxy base URLs per provider.
# github → Azure endpoint has no Helicone proxy; ollama is local — both skipped.
# gemini → deliberately NOT proxied: Helicone's Google-AI-Studio gateway is
#   incompatible with litellm's native `gemini/` provider (it raises
#   "Missing target base url"), so Gemini calls go direct to Google.
_HELICONE_PROXIES: dict[str, str] = {
    "groq":   "https://groq.helicone.ai/openai/v1",
    "openai": "https://oai.helicone.ai/v1",
}


def provider_chain() -> list[tuple[str, dict[str, Any]]]:
    """Return [(model_str, litellm_kwargs), ...] in configured priority order.

    FORCE_LOCAL_OLLAMA=true collapses the chain to Ollama only.
    Callers iterate this list and fall back to the next entry on exception.
    """
    if settings.force_local_ollama:
        log.info("FORCE_LOCAL_OLLAMA — routing all calls to Ollama")
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


def pick_crewai_llm():
    """Return the first available provider as a crewai.LLM object.

    The returned LLM has cache_breakpoint stripping and transparent provider
    fallback built in via _attach_resilient_call(). On 429/5xx the routing
    silently advances to the next provider without surfacing errors to CrewAI.
    When Helicone is configured, extra_headers are injected via
    additional_params so they flow through CrewAI → litellm → HTTP.
    FORCE_LOCAL_OLLAMA forces Ollama regardless of other settings.
    """
    from crewai import LLM

    if settings.force_local_ollama:
        log.info("LLM router: FORCE_LOCAL_OLLAMA — using Ollama %s", settings.ollama_model)
        chain = provider_chain()
        llm = _crewai_ollama()
        _attach_resilient_call(llm, chain)
        return llm

    if settings.environment.lower() == "aws":
        log.info("LLM router: using Bedrock %s (aws mode)", settings.bedrock_model_id)
        return LLM(model=f"bedrock/{settings.bedrock_model_id}", temperature=0.2)

    chain = provider_chain()
    for name in settings.llm_provider_chain.split(","):
        name = name.strip().lower()
        entry = _build_entry(name)
        if entry is None:
            continue
        model, kwargs = entry
        log.info(
            "LLM router: selected provider=%s model=%s helicone=%s",
            name, model, "extra_headers" in kwargs,
        )
        llm = _entry_to_crewai_llm(name, model, kwargs)
        _attach_resilient_call(llm, chain, fallback=True)
        return llm

    raise RuntimeError(
        "No LLM provider available — set at least one API key or ensure Ollama "
        "is reachable. Chain: " + settings.llm_provider_chain
    )


def pick_crewai_llm_pinned(target_model: str):
    """Return a crewai.LLM pinned to target_model — NO resilient fallback chain.

    For EVAL benchmark runs: the test matrix requires each configuration to
    use EXACTLY the specified model. Attaching _attach_resilient_call would
    silently swap providers on 429/5xx, producing invalid comparison data.

    Model string resolution follows LiteLLM conventions:
      "groq/llama-3.3-70b-versatile"  → Groq API key
      "gemini/gemini-2.5-flash"        → Google API key
      "gpt-4o" / "openai/gpt-4o"      → OpenAI API key
      "openai/<anything>"              → OpenAI API key
      "github/<anything>"              → GitHub Models (Azure endpoint)
      "openai/<model>" + ollama_url    → Ollama (when force_local_ollama)

    Raises:
        RuntimeError: if the API key required for target_model is not configured,
                      or if target_model cannot be resolved to any known provider.
    """
    from crewai import LLM
    _force_litellm_routing(LLM)

    model_lower = target_model.lower()

    # ── Groq ──────────────────────────────────────────────────────────────────
    if model_lower.startswith("groq/"):
        key = settings.groq_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                f"EVAL: target_model={target_model!r} requires AGENTIC_GROQ_API_KEY "
                "which is not set. Set the key or choose a different target_model."
            )
        kwargs: dict = {"api_key": key}
        kwargs.update(_helicone_overrides("groq"))
        log.info("EVAL LLM router: pinned provider=groq model=%s", target_model)
        _set_global_litellm_key("groq", key)
        llm = _entry_to_crewai_llm("groq", target_model, kwargs)
        _attach_resilient_call(llm, [], fallback=False)
        return llm

    # ── Gemini ────────────────────────────────────────────────────────────────
    if model_lower.startswith("gemini/"):
        key = settings.google_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                f"EVAL: target_model={target_model!r} requires AGENTIC_GOOGLE_API_KEY "
                "which is not set."
            )
        kwargs = {"api_key": key}
        log.info("EVAL LLM router: pinned provider=gemini model=%s", target_model)
        _set_global_litellm_key("gemini", key)
        llm = _entry_to_crewai_llm("gemini", target_model, kwargs)
        _attach_resilient_call(llm, [], fallback=False)
        return llm

    # ── OpenAI (gpt-* shorthand or openai/ prefix) ────────────────────────────
    if model_lower.startswith("gpt-") or model_lower.startswith("openai/"):
        key = settings.openai_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                f"EVAL: target_model={target_model!r} requires AGENTIC_OPENAI_API_KEY "
                "which is not set."
            )
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("openai"))
        log.info("EVAL LLM router: pinned provider=openai model=%s", target_model)
        _set_global_litellm_key("openai", key)
        llm = _entry_to_crewai_llm("openai", target_model, kwargs)
        _attach_resilient_call(llm, [], fallback=False)
        return llm

    # ── GitHub Models (Azure OpenAI-compat endpoint) ──────────────────────────
    if model_lower.startswith("github/"):
        key = settings.github_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                f"EVAL: target_model={target_model!r} requires AGENTIC_GITHUB_API_KEY "
                "which is not set."
            )
        kwargs = {"api_key": key, "api_base": settings.github_base_url}
        log.info("EVAL LLM router: pinned provider=github model=%s", target_model)
        llm = _entry_to_crewai_llm("github", target_model, kwargs)
        _attach_resilient_call(llm, [], fallback=False)
        return llm

    # ── Ollama ────────────────────────────────────────────────────────────────
    if model_lower.startswith("ollama/"):
        ollama_model = target_model.split("/", 1)[1]
        kwargs = {
            "api_key": "ollama",
            "api_base": f"{settings.ollama_url.rstrip('/')}/v1",
        }
        log.info("EVAL LLM router: pinned provider=ollama model=%s", ollama_model)
        llm = _entry_to_crewai_llm("ollama", f"openai/{ollama_model}", kwargs)
        _attach_resilient_call(llm, [], fallback=False)
        return llm

    raise RuntimeError(
        f"EVAL: Cannot resolve target_model={target_model!r} to a known provider. "
        "Use a LiteLLM-prefixed string: 'groq/<model>', 'gemini/<model>', "
        "'gpt-<model>', 'openai/<model>', 'github/<model>', or 'ollama/<model>'."
    )


# ── internal ──────────────────────────────────────────────────────────────────

def _force_litellm_routing(LLM) -> None:
    """Neutralise CrewAI's native-provider selection (idempotent).

    Newer CrewAI pattern-matches any ``gemini/gemini-*`` tag to its *native*
    google-genai SDK (not installed here), and ``_get_native_provider`` performs
    the crashing import *before* the ``is_litellm`` guard can take effect — so
    ``is_litellm=True`` alone isn't enough. This whole router is litellm-only by
    design (the resilient-fallback swap mutates litellm model strings on one LLM
    instance), so we make native selection a no-op: ``_get_native_provider``
    returns ``None`` → construction falls through to LiteLLM with the full,
    prefixed model string intact.
    """
    if getattr(LLM, "_qst_native_disabled", False):
        return
    LLM._get_native_provider = classmethod(lambda cls, provider: None)
    LLM._qst_native_disabled = True


def _entry_to_crewai_llm(name: str, model: str, kwargs: dict[str, Any]):
    """Convert a provider_chain entry into a crewai.LLM object."""
    from crewai import LLM
    _force_litellm_routing(LLM)

    # Gemini 3.x degrades / can infinite-loop below temperature 1.0 (LiteLLM
    # warns explicitly); keep the deterministic 0.2 for every other provider.
    temperature = 1.0 if "gemini-3" in str(model) else 0.2
    crewai_kwargs: dict[str, Any] = {"temperature": temperature}

    if "api_key" in kwargs:
        crewai_kwargs["api_key"] = kwargs["api_key"]
        # Also set global litellm keys so instructor / structured-output calls
        # that bypass the LLM wrapper can still authenticate
        _set_global_litellm_key(name, kwargs["api_key"])

    if "api_base" in kwargs:
        crewai_kwargs["base_url"] = kwargs["api_base"]
        # Ollama: instructor uses OpenAI-compat endpoint — point globals there
        if name == "ollama":
            os.environ["OPENAI_API_BASE"] = kwargs["api_base"]
            litellm.api_base = kwargs["api_base"]

    if "extra_headers" in kwargs:
        crewai_kwargs["additional_params"] = {"extra_headers": kwargs["extra_headers"]}

    # Force the LiteLLM path: this router (and the resilient-fallback swap in
    # _attach_resilient_call) manipulates litellm-style model strings on a single
    # LLM instance. CrewAI's *native* provider SDKs (e.g. gemini → google-genai,
    # which isn't installed) would both crash at construction for newer model
    # tags and break the model-swap fallback. is_litellm=True keeps every
    # provider on the uniform LiteLLM codepath.
    return LLM(model=model, is_litellm=True, **crewai_kwargs)


def _crewai_ollama():
    """Build a CrewAI LLM for local Ollama (handles global env setup)."""
    entry = _build_entry("ollama")
    if entry is None:
        raise RuntimeError("Ollama entry could not be built")
    model, kwargs = entry
    return _entry_to_crewai_llm("ollama", model, kwargs)


# ── Empty-response resilience ─────────────────────────────────────────────────
# Gemini (and occasionally other providers via LiteLLM) intermittently return a
# completion with an empty `choices` list. CrewAI's structured-output path runs
# that response through `instructor`, whose openai handler does
# `response.choices[0].message` and raises IndexError → InstructorRetryException
# ("list index out of range"). Under the FULL swarm (more agents, larger context)
# this was crashing entire eval syntheses with HTTP 502. We retry a few times,
# then degrade gracefully so one flaky agent can't abort the whole run.

_AGENT_CALL_RETRIES = 3          # total attempts per provider before fallback/degrade
_EMPTY_RESPONSE_BACKOFF_S = 1.5  # backoff between empty-response retries
_RATE_LIMIT_BACKOFF_S = 5.0      # backoff between rate-limit retries (Groq TPM ~6-10s)

# A valid, neutral ProbabilityReport JSON. Engine.synthesize() overrides the
# identity fields (run_id/ticker/question/horizon_days/generated_at/engine_backend)
# but model_validate() still requires them present, so we include placeholders.
# Low confidence + an explicit caveat make degraded runs identifiable downstream.
_GRACEFUL_DEGRADATION_STUB = (
    '{"run_id":"degraded","ticker":"NA","question":"degraded","horizon_days":1,'
    '"generated_at":"1970-01-01T00:00:00+00:00","engine_backend":"crew-degraded",'
    '"probabilities":{"bullish":0.33,"neutral":0.34,"bearish":0.33},'
    '"technical_view":{"condition_score":0.0,"dominant_patterns":[],'
    '"rationale":"Graceful-degradation stub: the model returned an empty response after retries."},'
    '"fundamental_view":{"key_drivers":[],"rationale":"Degraded fallback — neutral stance assumed.","sources":[]},'
    '"risk_assessment":{"risk_level":"medium","key_risks":["incomplete analysis"],'
    '"max_position_pct":0.0,"notes":"graceful degradation"},'
    '"confidence":0.2,'
    '"caveats":["Graceful-degradation stub: upstream LLM returned no choices; treat as low-signal."],'
    '"execution_plan":null,"volatility_view":null,"space_economy_view":null,"forecast":null,"vision":null}'
)


def _is_empty_response_error(exc: Exception) -> bool:
    """True when an exception is the empty-`choices` / instructor-parse failure.

    Matches by message substring and by walking the __cause__/__context__ chain
    so it catches both the raw IndexError and the wrapping InstructorRetryException.
    """
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 6:
        text = f"{type(cur).__name__}: {cur}".lower()
        if (
            "list index out of range" in text
            or "instructorretry" in text
            or ("choices" in text and "index" in text)
        ):
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


def _attach_resilient_call(llm, chain: list[tuple[str, Any]], fallback: bool = True) -> None:
    """Replace llm.call() on this instance with a version that:
      1. Strips 'cache_breakpoint' from messages (Anthropic-only, rejected elsewhere)
      2. Falls back through the provider chain on 429/5xx before raising (if fallback=True)

    Patches the instance attribute only — the class method is untouched.
    _orig is a bound method capturing self=llm, so mutating llm.model et al.
    before calling _orig() redirects the underlying litellm call.
    """
    try:
        from crewai.llms.cache import CACHE_BREAKPOINT_KEY
    except ImportError:
        CACHE_BREAKPOINT_KEY = "cache_breakpoint"

    _RETRIABLE = (
        litellm.exceptions.RateLimitError,
        litellm.exceptions.ServiceUnavailableError,
        litellm.exceptions.APIConnectionError,
        litellm.exceptions.Timeout,
    )

    _orig = llm.call  # bound method; self=llm is captured

    def _resilient(messages, *args, **kwargs):
        # Strip Anthropic prompt-caching key that non-Anthropic providers reject
        if isinstance(messages, list):
            messages = [
                {k: v for k, v in m.items() if k != CACHE_BREAKPOINT_KEY}
                if isinstance(m, dict) else m
                for m in messages
            ]

        # Primary provider attempts. Transient failures (rate-limit / empty
        # response) get bounded in-place retries first; other failures activate
        # the fallback chain (when enabled) or degrade gracefully so a single
        # flaky agent can't 502 the whole synthesis.
        saw_empty_response = False
        for attempt in range(1, _AGENT_CALL_RETRIES + 1):
            try:
                return _orig(messages, *args, **kwargs)
            except _RETRIABLE as exc:
                # Rate-limit / transient server errors: back off and retry the
                # SAME provider (Groq free-tier TPM clears in ~6-10s).
                if attempt < _AGENT_CALL_RETRIES:
                    log.warning(
                        "Provider %s transient %s (attempt %d/%d); backing off",
                        llm.model, type(exc).__name__, attempt, _AGENT_CALL_RETRIES,
                    )
                    time.sleep(_RATE_LIMIT_BACKOFF_S * attempt)
                    continue
                if fallback:
                    log.warning("Provider %s → %s after retries; activating fallback chain",
                                llm.model, type(exc).__name__)
                    break
                # Pinned-model path (eval): surface the failure so the caller
                # (run_eval_matrix) can fall through to a working provider for
                # REAL data instead of recording a degraded stub.
                log.warning("Provider %s → %s after retries; re-raising for caller fallback",
                            llm.model, type(exc).__name__)
                raise
            except Exception as exc:
                if _is_empty_response_error(exc):
                    saw_empty_response = True
                    if attempt < _AGENT_CALL_RETRIES:
                        log.warning(
                            "Provider %s empty/parse failure (attempt %d/%d: %s); retrying",
                            llm.model, attempt, _AGENT_CALL_RETRIES, str(exc)[:120],
                        )
                        time.sleep(_EMPTY_RESPONSE_BACKOFF_S * attempt)
                        continue
                if fallback:
                    log.warning("Provider %s failed (%s); activating fallback chain: %s",
                                llm.model, type(exc).__name__, str(exc)[:160])
                    break
                # Pinned-model path (eval): surface the failure so the caller
                # (run_eval_matrix) can fall through to a working provider for
                # REAL data instead of recording a degraded stub.
                log.warning("Provider %s failed (%s); re-raising for caller fallback: %s",
                            llm.model, type(exc).__name__, str(exc)[:160])
                raise

        # Walk the chain, skipping the provider that just failed
        primary = str(llm.model)
        for fb_model, fb_kwargs in chain:
            if str(fb_model) == primary:
                continue
            _saved = {
                "model":             llm.model,
                "api_key":           llm.api_key,
                "base_url":          getattr(llm, "base_url", None),
                "additional_params": dict(getattr(llm, "additional_params", {}) or {}),
            }
            try:
                llm.model             = fb_model
                llm.api_key           = fb_kwargs.get("api_key", "")
                llm.base_url          = fb_kwargs.get("api_base")
                llm.additional_params = (
                    {"extra_headers": fb_kwargs["extra_headers"]}
                    if "extra_headers" in fb_kwargs else {}
                )
                # mirror the key into litellm globals/env — the instance was
                # built for the primary provider, so e.g. GEMINI_API_KEY may
                # be unset on this codepath.
                _mirror_fallback_key(str(fb_model), fb_kwargs.get("api_key", ""))
                log.info("Fallback: trying model=%s", fb_model)
                result = _orig(messages, *args, **kwargs)
                log.info("Fallback succeeded: model=%s", fb_model)
                return result
            except Exception as e:
                # Once we're in the fallback chain we exhaust EVERY remaining
                # provider before giving up — a rate-limit, quota, expired-key,
                # or other auth failure on one secondary provider must not abort
                # the chain (the local Ollama last resort should still be tried).
                if _is_empty_response_error(e):
                    saw_empty_response = True
                log.warning("Fallback %s failed (%s): %s",
                            fb_model, type(e).__name__, str(e)[:160])
            finally:
                llm.model             = _saved["model"]
                llm.api_key           = _saved["api_key"]
                llm.base_url          = _saved["base_url"]
                llm.additional_params = _saved["additional_params"]

        # Every provider exhausted. Degrade gracefully rather than 502-ing the
        # whole synthesis — a single agent failing should not abort the run.
        log.error(
            "All providers exhausted (primary=%s, chain=%s); returning "
            "graceful-degradation stub (saw_empty_response=%s)",
            primary, [m for m, _ in chain], saw_empty_response,
        )
        return _GRACEFUL_DEGRADATION_STUB

    llm.call = _resilient


def _set_global_litellm_key(provider: str, key: str) -> None:
    """Mirror API key into litellm global registry for non-CrewAI codepaths."""
    if provider == "groq":
        litellm.groq_key = key
    elif provider in ("openai", "github"):
        litellm.openai_key = key
        os.environ["OPENAI_API_KEY"] = key
    elif provider in ("gemini", "gemini_flash"):
        litellm.vertex_key = key
        os.environ["GEMINI_API_KEY"] = key


def _mirror_fallback_key(model: str, key: str) -> None:
    """Infer the provider from a fallback model string and mirror its key."""
    if not key:
        return
    if model.startswith("groq/"):
        _set_global_litellm_key("groq", key)
    elif model.startswith("gemini/"):
        _set_global_litellm_key("gemini", key)
    elif model.startswith("gpt") or model.startswith("openai/"):
        _set_global_litellm_key("openai", key)


def _build_entry(name: str) -> tuple[str, dict[str, Any]] | None:
    if name == "groq":
        key = settings.groq_api_key.get_secret_value()
        if not key:
            log.debug("Skipping groq — no AGENTIC_GROQ_API_KEY")
            return None
        kwargs: dict[str, Any] = {"api_key": key}
        kwargs.update(_helicone_overrides("groq"))
        return (f"groq/{settings.groq_model}", kwargs)

    if name == "openai":
        key = settings.openai_api_key.get_secret_value()
        if not key:
            log.debug("Skipping openai — no AGENTIC_OPENAI_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("openai"))
        return (settings.openai_model, kwargs)

    if name == "gemini":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini — no AGENTIC_GOOGLE_API_KEY")
            return None
        kwargs = {"api_key": key}
        kwargs.update(_helicone_overrides("gemini"))
        return (settings.gemini_model, kwargs)

    if name == "gemini_flash":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.debug("Skipping gemini_flash — no AGENTIC_GOOGLE_API_KEY")
            return None
        # Direct (no Helicone proxy — same google-ai-studio incompatibility).
        return (settings.gemini_flash_model, {"api_key": key})

    if name == "github":
        key = settings.github_api_key.get_secret_value()
        if not key:
            log.debug("Skipping github — no AGENTIC_GITHUB_API_KEY")
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

    log.warning("Unknown provider in AGENTIC_LLM_PROVIDER_CHAIN: %r", name)
    return None


def _helicone_overrides(provider_name: str) -> dict[str, Any]:
    """Return api_base + extra_headers to route through Helicone.

    Also injects Helicone-Property-Provider so you can filter by provider
    in the Helicone dashboard and see exactly when spend spills from Groq
    (free) into OpenAI or Gemini (paid).
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
        "Helicone-Property-Provider": provider_name,  # cost-tier tracking
    }
    if settings.helicone_cache_enabled:
        headers["Helicone-Cache-Enabled"] = "true"

    log.debug("Helicone: routing %s → %s (cache=%s)",
              provider_name, proxy_base, settings.helicone_cache_enabled)
    return {
        "api_base": proxy_base,
        "extra_headers": headers,
    }
