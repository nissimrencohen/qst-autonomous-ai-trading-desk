"""Concurrent batch analysis for the 10 whitelisted instruments.

POST /analyze/batch runs all tickers in parallel using asyncio + a
semaphore so we never flood the LLM router with more than
AGENTIC_BATCH_CONCURRENCY simultaneous requests (default 3).

The semaphore is the primary rate-limit guard; the LLM router's own
exponential-backoff handles any residual 429s.

Each ticker gets its own run_id and follows the identical path as a
single POST /analyze — guardrails → RAG → vision → CrewAI synthesis →
gatekeeper. The batch response includes all run_ids immediately; callers
poll GET /runs/{run_id} for individual results.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.gatekeeper import WHITELIST
from app.orchestrator import run_analysis_job
from app.schemas import AnalyzeRequest

log = logging.getLogger(__name__)


@dataclass
class BatchTickerResult:
    ticker: str
    run_id: str
    status: str  # "started" | "invalid_ticker"
    reason: str = ""


async def run_batch(
    tickers: list[str],
    question: str,
    horizon_days: int,
    engine,
    runs,
    *,
    concurrency: int = 3,
    volatility_desk: bool = False,
    macro_context: str | None = None,
    interval: str = "1d",
) -> list[BatchTickerResult]:
    """Kick off analysis for each ticker in parallel, respecting the whitelist.

    Returns immediately once all background tasks are launched (not when they
    finish). The caller's HTTP handler returns the run_ids to the client, which
    then polls GET /runs/{id} for each result.
    """
    results: list[BatchTickerResult] = []
    sem = asyncio.Semaphore(concurrency)

    async def _one(ticker: str) -> BatchTickerResult:
        t = ticker.upper().lstrip("$").strip()
        if t not in WHITELIST:
            log.warning("batch: skipping non-whitelisted ticker=%s", t)
            return BatchTickerResult(
                ticker=t, run_id="", status="invalid_ticker",
                reason=f"{t} is not on the approved whitelist",
            )

        req = AnalyzeRequest(
            ticker=t,
            question=question,
            horizon_days=horizon_days,
            volatility_desk=volatility_desk,
            macro_context=macro_context,
            interval=interval,
        )
        run = runs.start(t)
        run.log("batch_analyze_received", {"ticker": t, "horizon_days": horizon_days})

        async with sem:
            # run_analysis_job is synchronous (blocking HTTP + LLM calls)
            # — offload to a thread to avoid blocking the event loop.
            task = asyncio.create_task(
                asyncio.to_thread(run_analysis_job, req, engine, run, runs)
            )

        return BatchTickerResult(ticker=t, run_id=run.run_id, status="started")

    coros = [_one(t) for t in tickers]
    gathered = await asyncio.gather(*coros, return_exceptions=False)
    results.extend(gathered)

    started = sum(1 for r in results if r.status == "started")
    skipped = len(results) - started
    log.info("batch launched: started=%d skipped=%d", started, skipped)
    return results
