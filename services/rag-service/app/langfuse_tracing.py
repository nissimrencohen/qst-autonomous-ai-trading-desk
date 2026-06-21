"""Langfuse LLM auto-logging for the RAG Service.

Registers litellm's built-in Langfuse callback so every LLM completion
(model, prompt, tokens, cost) is logged automatically as a Generation.
Complete no-op when RAG_LANGFUSE_PUBLIC_KEY / SECRET_KEY are unset.
Call configure_langfuse_litellm() once from main.py at startup.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def configure_langfuse_litellm() -> bool:
    """Register litellm Langfuse callback if keys are configured."""
    from app.config import settings

    pk = settings.langfuse_public_key.get_secret_value()
    sk = settings.langfuse_secret_key.get_secret_value()
    if not pk or not sk:
        log.debug(
            "Langfuse disabled — set RAG_LANGFUSE_PUBLIC_KEY and "
            "RAG_LANGFUSE_SECRET_KEY to enable"
        )
        return False

    try:
        import litellm
    except ImportError:
        log.warning("litellm not available; Langfuse callback not registered")
        return False

    os.environ["LANGFUSE_PUBLIC_KEY"] = pk
    os.environ["LANGFUSE_SECRET_KEY"] = sk
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    if "langfuse" not in litellm.success_callback:
        litellm.success_callback.append("langfuse")
    if not litellm.failure_callback:
        litellm.failure_callback = ["langfuse"]
    elif "langfuse" not in litellm.failure_callback:
        litellm.failure_callback.append("langfuse")

    log.info("Langfuse LLM auto-logging enabled — host=%s", settings.langfuse_host)
    return True
