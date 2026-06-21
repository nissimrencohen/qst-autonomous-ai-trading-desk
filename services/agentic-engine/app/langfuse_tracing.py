"""Langfuse prompt tracing for the Agentic Engine.

Two integration layers — both are complete no-ops when
AGENTIC_LANGFUSE_PUBLIC_KEY / AGENTIC_LANGFUSE_SECRET_KEY are unset.

Layer 1 — LiteLLM auto-logging (configure_langfuse_litellm):
  Registers litellm's built-in Langfuse callback so every LLM completion
  (model, prompt, completion, tokens, cost, latency) is logged automatically
  as a Langfuse Generation.  Call once at startup from main.py.

Layer 2 — Synthesis trace (synthesis_trace context manager):
  Wraps the full crew.kickoff() call with a Langfuse Trace so all three
  agent completions appear under a single named trace in the Langfuse UI.

  Usage in engine.py:

      from app.langfuse_tracing import build_langfuse_client, synthesis_trace

      class CrewEngine:
          def __init__(self):
              self._lf = build_langfuse_client()

          def synthesize(self, req, run):
              with synthesis_trace(self._lf, req, run) as lf_trace:
                  result = crew.kickoff(inputs=inputs)
                  ...
                  if lf_trace:
                      lf_trace.update(output={...})
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger(__name__)


# ── Layer 1: litellm auto-logging ─────────────────────────────────────────────

def configure_langfuse_litellm() -> bool:
    """Register litellm Langfuse callback if keys are configured.

    Sets LANGFUSE_* env vars expected by litellm's integration module and
    appends "langfuse" to litellm.success_callback / failure_callback.
    Safe to call multiple times (idempotent).
    """
    from app.config import settings

    pk = settings.langfuse_public_key.get_secret_value()
    sk = settings.langfuse_secret_key.get_secret_value()
    if not pk or not sk:
        log.debug(
            "Langfuse disabled — set AGENTIC_LANGFUSE_PUBLIC_KEY and "
            "AGENTIC_LANGFUSE_SECRET_KEY to enable"
        )
        return False

    try:
        import litellm
    except ImportError:
        log.warning("litellm not available; Langfuse callback not registered")
        return False

    # litellm's langfuse integration reads these env vars at call time
    os.environ["LANGFUSE_PUBLIC_KEY"] = pk
    os.environ["LANGFUSE_SECRET_KEY"] = sk
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    if "langfuse" not in litellm.success_callback:
        litellm.success_callback.append("langfuse")
    if not litellm.failure_callback:
        litellm.failure_callback = ["langfuse"]
    elif "langfuse" not in litellm.failure_callback:
        litellm.failure_callback.append("langfuse")

    log.info(
        "Langfuse LLM auto-logging enabled — host=%s", settings.langfuse_host
    )
    return True


# ── Layer 2: synthesis-level trace ────────────────────────────────────────────

def build_langfuse_client():
    """Return a configured Langfuse client, or None if Langfuse is not enabled.

    The returned client is intended to be stored on the engine instance so the
    Langfuse SDK's internal background queue is reused across requests.
    """
    from app.config import settings

    pk = settings.langfuse_public_key.get_secret_value()
    sk = settings.langfuse_secret_key.get_secret_value()
    if not pk or not sk:
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=pk,
            secret_key=sk,
            host=settings.langfuse_host,
        )
        log.info(
            "Langfuse synthesis tracing enabled — host=%s", settings.langfuse_host
        )
        return client
    except ImportError:
        log.warning(
            "langfuse package not installed; install langfuse>=2.0 to enable "
            "synthesis-level traces"
        )
        return None
    except Exception as exc:
        log.warning("Langfuse client init failed (%s); synthesis tracing disabled", exc)
        return None


@contextmanager
def synthesis_trace(
    lf_client,
    req: Any,
    run: Any,
) -> Generator:
    """Context manager: open a Langfuse Trace around crew.kickoff().

    Yields the StatefulTraceClient so the caller can attach the final output:

        with synthesis_trace(self._lf, req, run) as lf_trace:
            result = crew.kickoff(inputs=inputs)
            report = ...
            if lf_trace:
                lf_trace.update(
                    output={"bullish": report.probabilities.bullish, ...}
                )

    All exceptions in lf_client calls are caught so a Langfuse outage never
    prevents synthesis from completing.
    """
    if lf_client is None:
        yield None
        return

    trace = None
    try:
        trace = lf_client.trace(
            name=f"synthesis/{req.ticker.upper()}",
            user_id=req.ticker.upper(),
            session_id=run.run_id,
            input={
                "ticker": req.ticker.upper(),
                "question": req.question,
                "horizon_days": req.horizon_days,
            },
            metadata={"run_id": run.run_id},
            tags=[req.ticker.upper(), "synthesis"],
        )
    except Exception as exc:
        log.warning("Langfuse trace creation failed (%s); continuing without trace", exc)
        yield None
        return

    try:
        yield trace
    finally:
        # Flush the SDK's internal queue so the trace is sent even on exception
        try:
            lf_client.flush()
        except Exception:
            pass
