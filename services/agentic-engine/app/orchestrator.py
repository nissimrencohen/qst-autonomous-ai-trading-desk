"""Server-side orchestration for the async POST /analyze endpoint.

Runs the full desk chain — guardrails (input rail) → RAG retrieval → optional
vision → CrewAI synthesis — as a single background job. The HTTP handler returns
a run_id immediately; the dashboard polls GET /runs/{id}. This replaces the
legacy "n8n blocks on the whole crew, browser waits, 120 s AbortSignal fires"
flow that produced `TimeoutError: signal timed out`.

The chain is intentionally self-contained (it calls the sibling services itself)
so it is independently testable with a single `curl -X POST :8003/analyze` and
works even when n8n is down. n8n remains the front-door orchestrator and simply
dispatches here, returning the run_id to the browser.

All network + crew work is synchronous and CPU/IO-bound, so the API layer runs
this via `asyncio.to_thread`.
"""
from __future__ import annotations

import base64
import logging

import requests

from app.config import settings
from app.runs import RunHandle
from app.schemas import AnalyzeRequest, RagInput, SynthesizeRequest, VisionInput
from app.social_pipeline import get_social_context

log = logging.getLogger(__name__)


def _validate_input(req: AnalyzeRequest) -> tuple[bool, list[str]]:
    """Call the guardrails input rail. Degrades OPEN (proceeds) on outage so a
    guardrails hiccup never takes the whole desk down — the output rail and the
    mandatory report caveats still apply downstream."""
    try:
        r = requests.post(
            f"{settings.guardrails_url}/validate/input",
            json={"question": req.question, "ticker": req.ticker, "source": "agentic-orchestrator"},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("guardrails returned %s; degrading open", r.status_code)
            return True, []
        body = r.json()
        reasons = [v.get("detail", "") for v in body.get("violations", [])]
        return bool(body.get("allowed", True)), reasons
    except Exception as exc:
        log.warning("guardrails unreachable (%s); degrading open", exc)
        return True, []


def _rag_query(req: AnalyzeRequest) -> RagInput:
    r = requests.post(
        f"{settings.rag_url}/query",
        json={"ticker": req.ticker, "question": req.question, "k": 4},
        timeout=settings.orchestrator_http_timeout_s,
    )
    r.raise_for_status()
    # RagInput ignores the extra ticker/distance/backend fields in the response.
    return RagInput.model_validate(r.json())


def _vision(req: AnalyzeRequest) -> VisionInput | None:
    if not req.chart_base64:
        return None
    try:
        img = base64.b64decode(req.chart_base64)
        files = {"chart": ("chart", img, req.chart_content_type or "image/png")}
        r = requests.post(
            f"{settings.vision_url}/analyse",
            files=files,
            data={"ticker": req.ticker},
            timeout=settings.orchestrator_http_timeout_s,
        )
        if r.status_code != 200:
            log.warning("vision returned %s; continuing without technical leg", r.status_code)
            return None
        return VisionInput.model_validate(r.json())
    except Exception as exc:
        log.warning("vision analysis failed (%s); continuing without it", exc)
        return None


def _apply_output_rail(report, rag: RagInput, run: RunHandle):
    """Run the guardrails OUTPUT rail over the report prose (preserved from the
    legacy n8n flow). On sanitize/block we surface it as an extra caveat rather
    than dropping the report — the dashboard still renders, flagged."""
    try:
        prose_parts = [
            report.fundamental_view.rationale,
            report.technical_view.rationale,
            report.risk_assessment.notes,
            *report.fundamental_view.key_drivers
        ]
        prose = " ".join([p for p in prose_parts if p]).strip()
        evidence = [d.text for d in rag.retrieved] + ([rag.summary] if rag.summary else [])
        r = requests.post(
            f"{settings.guardrails_url}/validate/output",
            json={"text": prose or "n/a", "evidence": evidence},
            timeout=30,
        )
        if r.status_code != 200:
            return report, []
        verdict = r.json()
        run.log("output_rail", {"action": verdict.get("action"), "violations": len(verdict.get("violations", []))})
        if verdict.get("action") != "pass":
            reasons = [v.get("rule", "Output blocked") for v in verdict.get("violations", [])]
            return None, reasons
        return report, []
    except Exception as exc:
        log.warning("output rail skipped (%s)", exc)
        return report, []


def run_analysis_job(req: AnalyzeRequest, engine, run: RunHandle, runs) -> None:
    """Blocking pipeline executed in a worker thread; results land on the run."""
    try:
        allowed, reasons = _validate_input(req)
        run.log("guardrails", {"allowed": allowed, "violations": len(reasons)})
        if not allowed:
            runs.set_blocked(run.run_id, reasons or ["Request is outside desk policy."])
            return

        rag = _rag_query(req)
        run.log("rag_query", {"retrieved": len(rag.retrieved), "has_summary": bool(rag.summary)})

        vision = _vision(req)
        run.log("vision", {"present": vision is not None})

        social = get_social_context(req.ticker)
        run.log("social_signals", {"ticker": req.ticker.upper(), "has_signals": bool(social)})

        # ── Mandatory Macro & Fear context (mission Req 2) ───────────────────
        # Every single-ticker analysis is assessed against the broad market
        # (S&P/NASDAQ) and the fear index (VIX). A caller-supplied macro_context
        # (e.g. the daily briefing's richer block) is respected; otherwise we
        # build one here so it is present for EVERY /analyze, not just briefings.
        macro_context = req.macro_context
        if not macro_context:
            from app.macro_context import build_desk_context
            macro_context = build_desk_context(req.ticker)
        run.log("macro_context", {
            "ticker": req.ticker.upper(),
            "source": "caller" if req.macro_context else "desk_auto",
            "chars": len(macro_context),
        })

        sreq = SynthesizeRequest(
            ticker=req.ticker,
            question=req.question,
            horizon_days=req.horizon_days,
            rag=rag,
            vision=vision,
            volatility_desk=req.volatility_desk,
            macro_context=macro_context,
            social_context=social,
        )
        report = engine.synthesize(sreq, run)
        report, out_reasons = _apply_output_rail(report, rag, run)
        if report is None:
            runs.set_blocked(run.run_id, ["Report flagged for human review"] + out_reasons)
            return

        # ── Execution Gatekeeper (v1.4) ──────────────────────────────────────
        # Enforce whitelist + broker routing on the execution plan.
        # Analysis report always flows through; only the ExecutionPlan leg is gated.
        from app.gatekeeper import enforce as gatekeeper_enforce
        gk = gatekeeper_enforce(report, run.run_id)
        report = gk.report
        run.log("gatekeeper", {
            "ticker": report.ticker,
            "execution_allowed": gk.execution_allowed,
            "violations": gk.violation_reasons,
            "broker_status": gk.order.status if gk.order else None,
        })
        # predictive chart — drift tilted by the crew's directional bias (Phase 3)
        try:
            from app.forecast import build_forecast
            fc = build_forecast(
                req.ticker, req.horizon_days,
                report.probabilities.bullish, report.probabilities.bearish,
                interval=req.interval,
            )
            if fc is not None:
                report = report.model_copy(update={"forecast": fc})
                run.log("forecast", {"interval": fc.interval, "bias": fc.directional_bias,
                                     "points": len(fc.projection)})
        except Exception as exc:
            log.warning("forecast attach failed run_id=%s: %s", run.run_id, exc)
        # Surface the chart-vision read on the report so the UI can show that the
        # uploaded chart was actually analysed and what it concluded.
        if vision is not None:
            report = report.model_copy(update={"vision": vision})
            run.log("vision_attached", {"label": vision.label, "score": round(vision.score, 3),
                                        "confidence": round(vision.confidence, 3)})
        runs.set_report(run.run_id, report)
        log.info("analyze done run_id=%s ticker=%s", run.run_id, req.ticker.upper())
    except Exception as exc:  # noqa: BLE001 — surface any failure on the run
        log.exception("analyze job failed run_id=%s ticker=%s", run.run_id, req.ticker)
        runs.set_error(run.run_id, f"{type(exc).__name__}: {exc}")
