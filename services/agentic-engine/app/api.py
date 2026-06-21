"""Agentic Engine API routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.orchestrator import run_analysis_job
from app.schemas import AnalyzeRequest, ProbabilityReport, RunTrace, SynthesizeRequest
from app.watchlist import WATCHLIST_ORDERED

log = logging.getLogger(__name__)
router = APIRouter()

AuthDep = Annotated[str | None, Depends(require_auth)]


@router.post("/analyze", tags=["agents"])
async def analyze(payload: AnalyzeRequest, request: Request, _auth: AuthDep = None) -> dict:
    """Kick off the full desk chain as a background job; return a run_id at once.

    The browser polls GET /runs/{run_id} for the live trace + final report.
    This is the dashboard's primary analysis entrypoint — see orchestrator.py.
    """
    engine = request.app.state.engine
    runs = request.app.state.runs
    run = runs.start(payload.ticker)
    run.log("analyze_received", {"ticker": payload.ticker.upper(), "horizon_days": payload.horizon_days})

    task = asyncio.create_task(asyncio.to_thread(run_analysis_job, payload, engine, run, runs))
    bg: set = request.app.state.bg_tasks
    bg.add(task)
    task.add_done_callback(bg.discard)

    return {"run_id": run.run_id, "status": "running"}


# ── Batch analysis (v1.4) ─────────────────────────────────────────────────────

class BatchAnalyzeRequest(BaseModel):
    """Run all (or a subset of) the 10 whitelisted tickers concurrently."""
    tickers: list[str] = Field(
        default_factory=lambda: list(WATCHLIST_ORDERED),
        min_length=1, max_length=10,
        description="Ticker symbols to analyse. Non-whitelisted symbols are rejected and listed under 'skipped'.",
    )
    question: str = Field(default="What is the outlook for this asset?", min_length=3, max_length=500)
    horizon_days: int = Field(default=30, ge=1, le=365)
    volatility_desk: bool = False
    macro_context: str | None = None
    interval: str = "1d"


@router.post("/analyze/batch", tags=["agents"])
async def analyze_batch(payload: BatchAnalyzeRequest, request: Request, _auth: AuthDep = None) -> dict:
    """Launch concurrent analysis for up to 7 whitelisted instruments.

    Returns run_ids immediately; poll GET /runs/{id} for each result.
    Non-whitelisted tickers are blocked by the Execution Gatekeeper and
    listed under `skipped` in the response.
    """
    from app.batch_orchestrator import run_batch
    from app.config import settings

    engine = request.app.state.engine
    runs = request.app.state.runs

    results = await run_batch(
        tickers=payload.tickers,
        question=payload.question,
        horizon_days=payload.horizon_days,
        engine=engine,
        runs=runs,
        concurrency=settings.batch_concurrency,
        volatility_desk=payload.volatility_desk,
        macro_context=payload.macro_context,
        interval=payload.interval,
    )

    started = [{"ticker": r.ticker, "run_id": r.run_id} for r in results if r.status == "started"]
    skipped = [{"ticker": r.ticker, "reason": r.reason} for r in results if r.status != "started"]
    return {"started": started, "skipped": skipped}


# ── Single synthesize (direct, no orchestrator) ───────────────────────────────

@router.post("/synthesize", response_model=ProbabilityReport, tags=["agents"])
def synthesize(payload: SynthesizeRequest, request: Request, _auth: AuthDep = None) -> ProbabilityReport:
    """Run the analyst crew over RAG + Vision outputs → probability report."""
    engine = request.app.state.engine
    runs = request.app.state.runs

    run = runs.start(payload.ticker)
    try:
        report = engine.synthesize(payload, run)
    except Exception:
        log.exception("synthesis failed run_id=%s ticker=%s", run.run_id, payload.ticker)
        runs.finish(run.run_id)
        raise HTTPException(502, "synthesis failed; see run trace") from None

    # Apply gatekeeper even on the direct synthesize path.
    from app.gatekeeper import enforce as gatekeeper_enforce
    gk = gatekeeper_enforce(report, run.run_id)
    report = gk.report
    run.log("gatekeeper", {
        "ticker": report.ticker,
        "execution_allowed": gk.execution_allowed,
        "violations": gk.violation_reasons,
    })
    runs.finish(run.run_id)

    log.info(
        "synthesized run_id=%s ticker=%s p_bull=%.2f risk=%s backend=%s gatekeeper=%s",
        report.run_id, report.ticker, report.probabilities.bullish,
        report.risk_assessment.risk_level, report.engine_backend,
        "allowed" if gk.execution_allowed else "BLOCKED",
    )
    return report


@router.get("/runs/{run_id}", response_model=RunTrace, tags=["agents"])
async def get_run(run_id: str, request: Request) -> RunTrace:
    """Agent execution trace for the dashboard's live log panel."""
    trace = request.app.state.runs.get(run_id)
    if trace is None:
        raise HTTPException(404, f"unknown run_id: {run_id}")
    return trace


@router.get("/memory/{ticker}", tags=["agents"])
async def get_memory(ticker: str, request: Request) -> dict:
    """Persisted per-ticker analysis history (agent_memory.db)."""
    turns = request.app.state.memory.load(ticker)
    return {"ticker": ticker.upper(), "turns": turns, "count": len(turns)}


# ── Gatekeeper info (v1.4) ────────────────────────────────────────────────────

@router.get("/gatekeeper/whitelist", tags=["gatekeeper"])
async def get_whitelist() -> dict:
    """Return the approved instrument whitelist."""
    from app.gatekeeper import WHITELIST
    return {"whitelist": sorted(WHITELIST)}


# ── Daily Morning Briefing (v1.4) ─────────────────────────────────────────────

@router.post("/daily-briefing/trigger", tags=["briefing"])
async def trigger_briefing(request: Request, _auth: AuthDep = None) -> dict:
    """Manually trigger the morning briefing pipeline (runs in background).

    Returns immediately with a status message. Poll GET /daily-briefing/latest
    for the result (typically ready within 2–5 minutes).
    """
    engine = request.app.state.engine
    runs   = request.app.state.runs
    store  = request.app.state.briefing_store

    from app.daily_briefing import run_daily_briefing
    task = asyncio.create_task(run_daily_briefing(engine, runs, store))
    bg: set = request.app.state.bg_tasks
    bg.add(task)
    task.add_done_callback(bg.discard)

    log.info("daily_briefing: manual trigger requested")
    return {"status": "triggered", "message": "Morning briefing started. Poll GET /daily-briefing/latest in ~3 minutes."}


@router.get("/daily-briefing/latest", tags=["briefing"])
async def get_latest_briefing(request: Request) -> dict:
    """Return the most recent daily briefing (persistent across restarts)."""
    store = request.app.state.briefing_store
    data  = store.get()
    if data is None:
        return {"status": "not_available", "message": "No briefing generated yet. POST /daily-briefing/trigger to generate one."}
    return data


# ── Ingestion Engine status (Step 2d) ─────────────────────────────────────────

@router.get("/ingestion/status", tags=["ingestion"])
async def ingestion_status() -> dict:
    """Ingestion cache snapshot for the Ingestion Dashboard. Never 500s —
    returns an `error` field if the DB can't be read yet."""
    from app.config import settings
    base = {
        "enabled": settings.ingestion_enabled,
        "interval_s": settings.ingestion_interval_s,
        "db_path": settings.ingestion_db_path,
        "total": 0, "by_source_type": {}, "by_ticker": [], "latest_ingested_at": None,
    }
    try:
        from app.ingestion_store import IngestionStore
        store = IngestionStore(settings.ingestion_db_path)
        try:
            base.update(store.stats())
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("ingestion_status read failed: %s", exc)
        base["error"] = str(exc)
    return base


# ── Continuous Synthesis Loop (Step 2e) ───────────────────────────────────────

@router.get("/synthesis/status", tags=["synthesis"])
async def synthesis_status(request: Request) -> dict:
    """Loop heartbeat: cursor, last ticker/status, last run time, report count."""
    store = getattr(request.app.state, "report_store", None)
    if store is None:
        return {"enabled": False, "message": "synthesis loop not initialised"}
    return {"enabled": True, **store.status()}


@router.get("/synthesis/latest", tags=["synthesis"])
async def synthesis_latest_all(request: Request) -> dict:
    """Latest continuously-synthesised report for every ticker (frontend poll)."""
    store = getattr(request.app.state, "report_store", None)
    reports = store.get_all() if store is not None else []
    return {"count": len(reports), "reports": reports}


@router.get("/synthesis/latest/{ticker}", tags=["synthesis"])
async def synthesis_latest_one(ticker: str, request: Request) -> dict:
    """Latest continuously-synthesised report for one ticker."""
    from app.watchlist import is_whitelisted, normalize
    t = normalize(ticker)
    if not is_whitelisted(t):
        raise HTTPException(400, f"{t} is not on the approved watchlist")
    store = getattr(request.app.state, "report_store", None)
    data = store.get(t) if store is not None else None
    if data is None:
        return {"status": "not_available", "ticker": t,
                "message": "No continuous report yet. Is the synthesis loop enabled?"}
    return data


@router.get("/daily-briefing/move-probs/{ticker}", tags=["briefing"])
def get_move_probs(ticker: str, horizon_days: int = 1) -> dict:
    """On-demand P(>X% move) for a single ticker.

    Uses historical vol from yfinance + GBM lognormal model.
    No CrewAI — instant response (~0.5 s).
    Thresholds: 1%, 2%, 3%, 5%, 10%.
    """
    from app.move_probability import instrument_probs
    from app.gatekeeper import is_whitelisted
    t = ticker.upper().lstrip("$").strip()
    if not is_whitelisted(t):
        from fastapi import HTTPException
        raise HTTPException(400, f"{t} is not on the approved whitelist")
    probs = instrument_probs(ticker=t, horizon_trading_days=max(1, min(horizon_days, 30)))
    return {"ticker": t, "horizon_trading_days": horizon_days, "move_probs": probs}
