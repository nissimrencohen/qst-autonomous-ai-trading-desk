"""Agentic Engine API routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.schemas import ProbabilityReport, RunTrace, SynthesizeRequest

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/synthesize", response_model=ProbabilityReport, tags=["agents"])
def synthesize(payload: SynthesizeRequest, request: Request) -> ProbabilityReport:
    """Run the analyst crew over RAG + Vision outputs -> probability report."""
    engine = request.app.state.engine
    runs = request.app.state.runs

    run = runs.start(payload.ticker)
    try:
        report = engine.synthesize(payload, run)
    except Exception:
        log.exception("synthesis failed run_id=%s ticker=%s", run.run_id, payload.ticker)
        runs.finish(run.run_id)
        raise HTTPException(502, "synthesis failed; see run trace") from None
    runs.finish(run.run_id)

    log.info(
        "synthesized run_id=%s ticker=%s p_bull=%.2f risk=%s backend=%s",
        report.run_id,
        report.ticker,
        report.probabilities.bullish,
        report.risk_assessment.risk_level,
        report.engine_backend,
    )
    return report


@router.get("/runs/{run_id}", response_model=RunTrace, tags=["agents"])
def get_run(run_id: str, request: Request) -> RunTrace:
    """Agent execution trace for the dashboard's live log panel."""
    trace = request.app.state.runs.get(run_id)
    if trace is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    return trace
