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
from app.eval_schemas import EvalSynthesizeRequest
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


# ── EVAL Research Lab: dynamic swarm + model synthesis ───────────────────────

@router.post("/eval/synthesize", response_model=ProbabilityReport, tags=["eval"])
def eval_synthesize(
    payload: EvalSynthesizeRequest,
    request: Request,
    _auth: AuthDep = None,
) -> ProbabilityReport:
    """Run the analyst crew with a dynamic EvalConfig and return a ProbabilityReport.

    This endpoint is the primary entry point for the EVAL Research Lab benchmark
    runner (Phase 2). It accepts the same synthesis payload as POST /synthesize
    but extends it with an ``eval_config`` block controlling:

      - ``swarm_size``: SOLO (1 agent), TRIAD (3 agents), or FULL (7 agents).
      - ``target_model``: Pinned LiteLLM model string — no cascade fallback.
      - ``experiment_name`` / ``run_label``: Tags written to every Langfuse
        trace, Phoenix eval score, and OTel span so the Phase 3 aggregation
        pipeline can group results by (experiment, config).

    The pipeline is identical to POST /synthesize — guardrails output rail and
    the Execution Gatekeeper are applied so EVAL scores are comparable to
    production runs. The only differences are the number of agents used and,
    when target_model is set, which LLM they call.

    Example request body::

        {
          "ticker": "NVDA",
          "question": "What is the 30-day outlook for NVDA given current macro conditions?",
          "horizon_days": 30,
          "rag": {"summary": null, "retrieved": []},
          "eval_config": {
            "experiment_name": "swarm_size_vs_model_impact",
            "run_label": "config_B_triad_gemini_flash",
            "swarm_size": "triad",
            "target_model": "gemini/gemini-2.5-flash"
          }
        }
    """
    from app.eval_schemas import EvalSynthesizeRequest  # local import avoids circular on startup

    engine = request.app.state.engine
    runs = request.app.state.runs

    run = runs.start(payload.ticker)
    run.log("eval_synthesize_received", {
        "ticker": payload.ticker.upper(),
        "experiment": payload.eval_config.experiment_name,
        "run_label": payload.eval_config.run_label,
        "swarm_size": payload.eval_config.swarm_size.value,
        "target_model": payload.eval_config.target_model or "cascade",
    })

    try:
        report = engine.synthesize(payload, run, eval_config=payload.eval_config)
    except RuntimeError as exc:
        # pick_crewai_llm_pinned raises RuntimeError when the target model's
        # API key is missing — surface this as a clear 422 for the runner.
        log.error(
            "eval_synthesize model config error run_id=%s: %s", run.run_id, exc
        )
        runs.finish(run.run_id)
        raise HTTPException(422, f"EVAL model config error: {exc}") from None
    except Exception:
        log.exception(
            "eval_synthesize failed run_id=%s ticker=%s experiment=%s",
            run.run_id, payload.ticker, payload.eval_config.experiment_name,
        )
        runs.finish(run.run_id)
        raise HTTPException(502, "eval synthesis failed; see run trace") from None

    # Apply the same output rail and gatekeeper as production /synthesize so
    # EVAL pipeline scores are computed against identical guardrail conditions.
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
        "eval_synthesize done run_id=%s ticker=%s experiment=%s swarm=%s model=%s "
        "p_bull=%.2f risk=%s gatekeeper=%s",
        report.run_id, report.ticker,
        payload.eval_config.experiment_name,
        payload.eval_config.swarm_size.value,
        payload.eval_config.target_model or "cascade",
        report.probabilities.bullish,
        report.risk_assessment.risk_level,
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
    report_store = getattr(request.app.state, "report_store", None)

    from app.daily_briefing import run_daily_briefing
    task = asyncio.create_task(run_daily_briefing(engine, runs, store, report_store=report_store))
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


# ── EVAL Research Lab: Phase 3 Data Aggregation endpoint ─────────────────────

@router.get("/eval/summary", tags=["eval"])
async def eval_summary(
    request: Request,
    experiment: str = "swarm_size_vs_model_impact",
    jsonl_file: str | None = None,
    no_langfuse: bool = False,
    no_phoenix: bool = False,
) -> dict:
    """Aggregate EVAL benchmark results into a dashboard-ready JSON payload.

    Fuses three data sources for the Phase 4 Next.js Research Dashboard:

      1. **Local JSONL** — Phase 2 runner output (latency, status, bullish,
         confidence, risk_level per matrix cell).
      2. **Langfuse API** — per-trace cost (USD), token usage, end-to-end
         latency, and eval scores (faithfulness, answer_relevancy,
         schema_compliance) attached by our eval_hooks pipeline.
      3. **Phoenix API** — per-run_id evaluation rows posted by the eval hooks,
         providing a cross-check on faithfulness and hallucination flags.

    Response is structured for direct consumption by:
      - Bar charts  → ``by_model[]``, ``by_swarm[]``
      - Scatter plot → ``scatter_data[]`` (cost_usd vs quality_score)
      - Table        → ``top_runs[]``
      - Conclusions  → ``conclusions`` dict with best-config recommendations

    Query Parameters:
        experiment:  Experiment name filter (default: swarm_size_vs_model_impact)
        jsonl_file:  Absolute or relative path to a specific JSONL result file.
                     Defaults to the latest eval_results_*.jsonl in ./data/
        no_langfuse: Set to true to skip Langfuse enrichment (offline/fast mode)
        no_phoenix:  Set to true to skip Phoenix enrichment (offline/fast mode)

    HTTP 200 — dashboard-ready JSON payload.
    HTTP 404 — no JSONL benchmark data found (run the Phase 2 runner first).
    HTTP 500 — unexpected aggregation error (check engine logs).
    """
    import os
    from pathlib import Path
    from functools import partial
    from app.config import settings

    # Resolve JSONL path
    jsonl_path: Path | None = None
    if jsonl_file:
        jsonl_path = Path(jsonl_file)
        if not jsonl_path.is_absolute():
            # Resolve relative to the working directory of the engine process
            jsonl_path = Path.cwd() / jsonl_path
        if not jsonl_path.exists():
            raise HTTPException(
                404,
                f"JSONL file not found: {jsonl_path}. "
                "Run `python scripts/run_eval_matrix.py` to generate benchmark data."
            )

    # Pull Langfuse credentials from the engine's settings
    # (same env vars used by langfuse_tracing.py)
    lf_pk   = getattr(settings, "langfuse_public_key",  None)
    lf_sk   = getattr(settings, "langfuse_secret_key",  None)
    lf_host = getattr(settings, "langfuse_host",        "http://langfuse:3000")
    if hasattr(lf_pk, "get_secret_value"):
        lf_pk = lf_pk.get_secret_value()
    if hasattr(lf_sk, "get_secret_value"):
        lf_sk = lf_sk.get_secret_value()

    phoenix_ep = getattr(settings, "phoenix_endpoint", "") or ""

    # The aggregation pipeline uses the Langfuse SDK which is synchronous.
    # Run it in a thread pool so we don't block the FastAPI event loop.
    # Load aggregate_eval_data.py dynamically from the volume mount (/srv/scripts) first
    # to avoid importing the stale script from the container's built-in /app/scripts.
    run_aggregation = None
    import importlib.util, sys
    
    spec_path = Path("/srv/scripts/aggregate_eval_data.py")
    if not spec_path.exists():
        spec_path = Path(__file__).parent.parent.parent / "scripts" / "aggregate_eval_data.py"
    if not spec_path.exists():
        spec_path = Path("/app/scripts/aggregate_eval_data.py")
        
    if spec_path.exists():
        try:
            spec = importlib.util.spec_from_file_location("aggregate_eval_data", spec_path)
            mod  = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            # Register in sys.modules BEFORE exec_module: dataclasses resolve their
            # own module via sys.modules during class creation, so an unregistered
            # module raises AttributeError on the first @dataclass. Registering up
            # front also means we re-exec the live /srv/scripts file on every call
            # (picking up edits) instead of serving a stale cached import.
            sys.modules["aggregate_eval_data"] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            run_aggregation = mod.run_aggregation
        except Exception as exc:
            log.warning("Failed to dynamically load aggregate_eval_data from %s: %s", spec_path, exc)
            sys.modules.pop("aggregate_eval_data", None)
            
    if run_aggregation is None:
        try:
            from scripts.aggregate_eval_data import run_aggregation  # type: ignore[import]
        except ImportError:
            raise HTTPException(
                500,
                "aggregate_eval_data.py not found. Tried /srv/scripts, parent /scripts, and /app/scripts"
            )

    # Prepare the aggregation call as a partial (runs in thread)
    agg_fn = partial(
        run_aggregation,
        jsonl_path       = jsonl_path,
        use_langfuse     = not no_langfuse,
        use_phoenix      = not no_phoenix,
        lf_public_key    = lf_pk   or "",
        lf_secret_key    = lf_sk   or "",
        lf_host          = lf_host,
        phoenix_endpoint = phoenix_ep,
        experiment_name  = experiment,
    )

    loop = asyncio.get_event_loop()
    try:
        payload = await loop.run_in_executor(None, agg_fn)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    except Exception as exc:
        log.exception("eval_summary aggregation failed: %s", exc)
        raise HTTPException(500, f"Aggregation failed: {exc}") from None

    return payload

