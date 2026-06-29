"""Post-synthesis evaluation hooks (Step 4 — LLMOps).

Three metrics computed after every synthesis, asynchronously in a background
thread pool so the HTTP 200 response is never delayed:

  schema_compliance  — deterministic: probs sum to 1.0, confidence ∈ [0,1],
                        risk_level is one of {low,medium,high}, caveats
                        non-empty.  Score: 0.0 (fail) | 1.0 (pass).

  faithfulness       — LLM-as-judge: does the output faithfully reflect the
                        RAG retrieval context?  Range: 0.0–1.0.

  answer_relevancy   — LLM-as-judge: is the output relevant to the analyst
                        question?  Range: 0.0–1.0.

The last two require ``AGENTIC_EVAL_BACKEND=deepeval`` and consume ~2-3 extra
LLM calls per synthesis.  They are skipped in the default ``schema`` backend.

Results are posted to:
  • Langfuse  — as Score objects linked to the synthesis trace_id
  • Arize Phoenix — via /v1/evaluations HTTP endpoint (opt-in)

Judge model selection (Q1 answer):
  • If ``AGENTIC_EVAL_JUDGE_MODEL`` is set (e.g. "gpt-4o"), that model is used
    exclusively for eval — no LLM router involvement.
  • Otherwise the first available provider in the existing llm_provider_chain
    is used, so zero extra configuration is needed.

Usage in engine.py::

    from app.eval_hooks import run_eval_async

    # at the end of synthesize(), after memory.save():
    run_eval_async(req, report, self._lf, run.run_id,
                   lf_trace_id=lf_trace.id if lf_trace else None)
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import time
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# Module-level executor — 2 workers keeps memory bounded; eval is never
# on the critical path so queuing is acceptable.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="eval-hook",
)


# ── 1. Schema compliance (deterministic) ─────────────────────────────────────


def _schema_compliance(report: Any) -> float:
    """Return 1.0 if the report passes all structural invariants, else 0.0.

    Checks performed (all must pass for a 1.0):
    - probabilities sum to 1.0 (±0.01 tolerance)
    - confidence is in [0.0, 1.0]
    - risk_level is one of 'low', 'medium', 'high'
    - caveats list is non-empty
    - ticker is non-empty
    """
    try:
        prob_sum = (
            report.probabilities.bullish
            + report.probabilities.neutral
            + report.probabilities.bearish
        )
        checks = [
            abs(prob_sum - 1.0) <= 0.01,
            0.0 <= report.confidence <= 1.0,
            report.risk_assessment.risk_level in ("low", "medium", "high"),
            len(report.caveats) > 0,
            len(report.ticker) >= 1,
        ]
        return 1.0 if all(checks) else 0.0
    except Exception as exc:
        log.warning("schema_compliance check raised: %s", exc)
        return 0.0


# ── 2. Judge LLM (LiteLLM-backed) ────────────────────────────────────────────


def _resolve_judge_model() -> str:
    """Return the litellm model string to use for LLM-as-judge evaluation.

    Priority:
    1. AGENTIC_EVAL_JUDGE_MODEL env var (explicit override — use a smarter
       model like gpt-4o without touching the main app's LLM chain).
    2. First provider in llm_provider_chain that has a key configured.
    """
    from app.config import settings

    if settings.eval_judge_model:
        return settings.eval_judge_model

    # Walk the same provider chain as llm_router but don't import it to avoid
    # loading crewai in the eval background thread unnecessarily.
    chain = [p.strip().lower() for p in settings.llm_provider_chain.split(",")]
    for provider in chain:
        if provider == "groq" and settings.groq_api_key.get_secret_value():
            return f"groq/{settings.groq_model}"
        if provider == "gemini" and settings.google_api_key.get_secret_value():
            return settings.gemini_model
        if provider == "openai" and settings.openai_api_key.get_secret_value():
            return settings.openai_model
        if provider == "github" and settings.github_api_key.get_secret_value():
            return settings.github_model

    # Ollama last resort — always available in local dev
    return f"ollama/{settings.ollama_model}"


def _build_deepeval_llm():
    """Build a DeepEvalBaseLLM that delegates to litellm.

    Returns None if deepeval is not installed.
    """
    model_id = _resolve_judge_model()

    try:
        try:
            from deepeval.models.base_model import DeepEvalBaseLLM
        except ImportError:
            from deepeval.models import DeepEvalBaseLLM  # fallback for older builds
        import litellm
    except ImportError:
        return None

    class _LiteLLMJudge(DeepEvalBaseLLM):  # type: ignore[misc]
        """Thin wrapper so DeepEval uses our litellm router instead of OpenAI."""

        def __init__(self) -> None:
            self._model = model_id

        def load_model(self) -> "_LiteLLMJudge":  # noqa: F821
            return self

        def generate(self, prompt: str, schema: Any = None) -> str:  # type: ignore[override]
            resp = litellm.completion(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                drop_params=True,
            )
            return resp.choices[0].message.content or ""

        async def a_generate(self, prompt: str, schema: Any = None) -> str:  # type: ignore[override]
            # Runs inside our ThreadPoolExecutor so no real event loop; delegate
            # to the sync method.
            return self.generate(prompt, schema)

        def get_model_name(self) -> str:
            return self._model

    return _LiteLLMJudge()


# ── 3. DeepEval LLM-judge metrics ────────────────────────────────────────────


def _deepeval_scores(req: Any, report: Any) -> dict[str, float]:
    """Run faithfulness + answer_relevancy via DeepEval.

    Returns an empty dict on any error (deepeval not installed, LLM failure,
    timeout, etc.) — eval failures are always non-fatal.
    """
    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError:
        log.warning(
            "deepeval package not installed; skipping LLM-judge metrics. "
            "Install deepeval>=1.4 to enable."
        )
        return {}

    judge = _build_deepeval_llm()
    if judge is None:
        log.warning("Could not build DeepEval judge LLM; skipping LLM-judge metrics.")
        return {}

    # Build retrieval context: RAG summary + individual document texts
    retrieval_context: list[str] = []
    if req.rag.summary:
        retrieval_context.append(req.rag.summary)
    for doc in req.rag.retrieved:
        retrieval_context.append(doc.text[:600])  # truncate to limit token cost
    if not retrieval_context:
        retrieval_context = ["No retrieval context was available for this synthesis."]

    # actual_output: key fields that should be faithful to and relevant for the question
    actual_output = json.dumps(
        {
            "ticker": report.ticker,
            "probabilities": report.probabilities.model_dump(),
            "confidence": report.confidence,
            "risk_level": report.risk_assessment.risk_level,
            "key_risks": report.risk_assessment.key_risks,
            "technical_rationale": report.technical_view.rationale,
            "fundamental_rationale": report.fundamental_view.rationale,
            "key_drivers": report.fundamental_view.key_drivers,
        },
        indent=None,
    )

    test_case = LLMTestCase(
        input=req.question,
        actual_output=actual_output,
        retrieval_context=retrieval_context,
    )

    metric_map = {
        "faithfulness": FaithfulnessMetric(
            model=judge,
            threshold=0.5,
            verbose_mode=False,
            include_reason=False,
        ),
        "answer_relevancy": AnswerRelevancyMetric(
            model=judge,
            threshold=0.5,
            verbose_mode=False,
            include_reason=False,
        ),
    }

    scores: dict[str, float] = {}
    for name, metric in metric_map.items():
        try:
            metric.measure(test_case)
            scores[name] = float(metric.score)
        except Exception as exc:
            log.warning("DeepEval metric '%s' failed: %s", name, exc)

    return scores


# ── 4. Result reporters ───────────────────────────────────────────────────────


def _post_to_langfuse(
    lf_client: Any,
    trace_id: str,
    scores: dict[str, float],
) -> None:
    """Attach eval scores to the Langfuse trace as Score objects.

    Uses trace_id (Langfuse's own UUID, not the server run_id) so scores
    appear under the correct synthesis trace in the Langfuse UI.
    """
    if lf_client is None or not scores:
        return
    for name, value in scores.items():
        try:
            lf_client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=f"eval_hook step4 | score={value:.3f}",
            )
        except Exception as exc:
            log.warning("Langfuse score post failed for '%s': %s", name, exc)


def _post_to_phoenix(
    scores: dict[str, float],
    req: Any,
    report: Any,
    run_id: str,
    eval_metadata: dict | None = None,
    otel_trace_id: str | None = None,
) -> None:
    """POST eval scores to Arize Phoenix /v1/trace_annotations if endpoint is set.

    We use the trace_id of the active request and set the annotation's identifier to run_id.

    Args:
        scores:        Metric name → float score mapping.
        req:           Original SynthesizeRequest (question + ticker).
        report:        Final ProbabilityReport.
        run_id:        Server-assigned run UUID (stored as annotation identifier).
        eval_metadata: Optional dict from EvalConfig.metadata_dict.
        otel_trace_id: The OpenTelemetry trace ID.
    """
    from app.config import settings

    if not settings.phoenix_endpoint:
        return

    if not otel_trace_id:
        import uuid
        otel_trace_id = uuid.uuid4().hex

    base_url = settings.phoenix_endpoint.rstrip("/")
    url = f"{base_url}/v1/trace_annotations"

    # Base score metadata present on every run (production + eval).
    base_score_meta: dict[str, Any] = {
        "ticker": req.ticker.upper(),
        "question": req.question[:120],
        "engine_backend": report.engine_backend,
        "horizon_days": req.horizon_days,
    }
    if eval_metadata:
        base_score_meta.update(eval_metadata)

    payload = {
        "data": [
            {
                "name": metric_name,
                "annotator_kind": "CODE" if metric_name == "schema_compliance" else "LLM",
                "trace_id": otel_trace_id,
                "result": {
                    "label": "pass" if score >= 0.5 else "fail",
                    "score": score,
                },
                "metadata": base_score_meta,
                "identifier": run_id,
            }
            for metric_name, score in scores.items()
        ]
    }

    data = json.dumps(payload).encode()
    http_req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_req, timeout=5) as resp:
            if resp.status not in (200, 201, 204):
                log.warning(
                    "Phoenix eval POST returned HTTP %s for run_id=%s",
                    resp.status,
                    run_id,
                )
            else:
                log.debug(
                    "Phoenix eval POST OK — run_id=%s metrics=%s",
                    run_id,
                    list(scores),
                )
    except Exception as exc:
        log.warning("Phoenix eval POST failed (run_id=%s): %s", run_id, exc)


# ── 5. Worker (runs inside the thread pool) ───────────────────────────────────


def _eval_worker(
    req: Any,
    report: Any,
    lf_client: Any,
    run_id: str,
    lf_trace_id: str | None,
    eval_metadata: dict | None = None,
    otel_trace_id: str | None = None,
) -> None:
    """Full eval pipeline — executed inside the background ThreadPoolExecutor."""
    from app.config import settings

    t0 = time.monotonic()
    scores: dict[str, float] = {}

    # Always run the deterministic schema compliance check
    scores["schema_compliance"] = _schema_compliance(report)

    # LLM-judge metrics only when explicitly opted in
    if settings.eval_backend == "deepeval":
        scores.update(_deepeval_scores(req, report))

    elapsed = round(time.monotonic() - t0, 2)
    log.info(
        "eval run_id=%s backend=%s scores=%s elapsed=%.2fs eval_experiment=%s",
        run_id,
        settings.eval_backend,
        {k: round(v, 3) for k, v in scores.items()},
        elapsed,
        (eval_metadata or {}).get("eval_experiment", "n/a"),
    )

    # Publish results — failures are logged but never re-raised
    _post_to_langfuse(lf_client, lf_trace_id or run_id, scores)
    _post_to_phoenix(scores, req, report, run_id, eval_metadata=eval_metadata, otel_trace_id=otel_trace_id)


# ── 6. Public API ─────────────────────────────────────────────────────────────


def run_eval_async(
    req: Any,
    report: Any,
    lf_client: Any,
    run_id: str,
    lf_trace_id: str | None = None,
    eval_metadata: dict | None = None,
) -> None:
    """Submit evaluation to the background thread pool (fire-and-forget).

    This function returns immediately.  The HTTP 200 response to the caller
    is never delayed by evaluation.  If the executor queue is full or the
    eval crashes, a WARNING is logged — it never propagates to the caller.

    Args:
        req:           SynthesizeRequest — original request (question + RAG context).
        report:        ProbabilityReport — final synthesis output to evaluate.
        lf_client:     Langfuse client instance (None → skip Langfuse scoring).
        run_id:        Server-assigned run UUID (for logging and Phoenix subject_id).
        lf_trace_id:   Langfuse trace ID if available (may differ from run_id).
                       Used to attach Score objects to the correct Langfuse trace.
        eval_metadata: Optional dict from EvalConfig.metadata_dict. Propagated
                       to Phoenix score metadata and Langfuse score comments so
                       all EVAL scores carry experiment/swarm_size/model tags.
    """
    from app.config import settings

    if settings.eval_backend == "none":
        log.debug("eval_backend=none — skipping eval for run_id=%s", run_id)
        return

    # Synchronously extract the active OpenTelemetry trace ID before submitting to background executor
    otel_trace_id = None
    try:
        from opentelemetry import trace as otel_trace
        current_span = otel_trace.get_current_span()
        if current_span:
            ctx = current_span.get_span_context()
            if ctx and ctx.is_valid:
                otel_trace_id = f"{ctx.trace_id:032x}"
    except Exception:
        pass

    try:
        _EXECUTOR.submit(
            _eval_worker, req, report, lf_client, run_id, lf_trace_id, eval_metadata, otel_trace_id
        )
        log.debug(
            "eval submitted for run_id=%s (backend=%s experiment=%s)",
            run_id,
            settings.eval_backend,
            (eval_metadata or {}).get("eval_experiment", "n/a"),
        )
    except RuntimeError as exc:
        # Executor was shut down (e.g. during process teardown)
        log.warning("eval_async submit failed (executor shutdown?): %s", exc)
    except Exception as exc:
        log.warning("eval_async submit failed: %s", exc)
