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


from langfuse.decorators import observe, langfuse_context

@contextmanager
@observe(name="synthesis")
def synthesis_trace(
    lf_client,
    req: Any,
    run: Any,
    eval_metadata: dict | None = None,
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

    Args:
        lf_client:     Langfuse client (None → no-op, Langfuse disabled).
        req:           SynthesizeRequest or EvalSynthesizeRequest.
        run:           RunHandle carrying the server run_id.
        eval_metadata: Optional dict produced by EvalConfig.metadata_dict.
                       When provided, its keys are merged into the trace
                       metadata and its EvalConfig.langfuse_tags are appended
                       to the trace tags. Pass None (default) for all
                       production (non-EVAL) synthesis calls — behaviour is
                       completely unchanged.

    All exceptions in lf_client calls are caught so a Langfuse outage never
    prevents synthesis from completing.
    """
    if lf_client is None:
        yield None
        return

    # Base tags: ticker + "synthesis". EVAL runs append experiment/run/model tags.
    base_tags = [req.ticker.upper(), "synthesis"]
    extra_tags: list[str] = []
    if eval_metadata:
        # Pull the human-readable eval tags from the flattened metadata dict.
        # EvalConfig.langfuse_tags is not available here (we only have the dict),
        # so reconstruct from the known keys.
        experiment = eval_metadata.get("eval_experiment", "")
        run_label = eval_metadata.get("eval_run_label", "")
        swarm_size = eval_metadata.get("eval_swarm_size", "")
        raw_model = eval_metadata.get("eval_target_model", "cascade")
        safe_model = raw_model.replace("/", "-").replace(":", "-")

        if experiment:
            extra_tags.append(experiment)
        if run_label:
            extra_tags.append(run_label)
        if swarm_size:
            extra_tags.append(swarm_size)
        extra_tags.append(f"model-{safe_model}")

    all_tags = base_tags + extra_tags

    # Base metadata; EVAL fields are merged in on top so they're always visible
    # alongside the standard run_id in the Langfuse trace metadata panel.
    base_metadata: dict[str, Any] = {"run_id": run.run_id}
    if eval_metadata:
        base_metadata.update(eval_metadata)

    try:
        langfuse_context.update_current_trace(
            name=f"synthesis/{req.ticker.upper()}",
            session_id=run.run_id,
            user_id=req.ticker.upper(),
            input={
                "ticker": req.ticker.upper(),
                "question": req.question,
                "horizon_days": req.horizon_days,
            },
            metadata=base_metadata,
            tags=all_tags,
        )
    except Exception as exc:
        log.warning("Langfuse trace context update failed (%s)", exc)

    trace = None
    try:
        trace = langfuse_context.get_current_trace()
    except Exception:
        pass

    try:
        yield trace
    finally:
        # Flush the SDK's internal queue so the trace is sent even on exception
        try:
            lf_client.flush()
        except Exception:
            pass

