"""Continuous Synthesis Loop (Step 2e).

An opt-in background task that walks the 10-ticker watchlist ONE AT A TIME on a
timer (default one ticker every 150 s → a full cycle ≈ 25 min). For each ticker
it runs a CrewAI synthesis whose tools read EXCLUSIVELY from the ingestion cache
(IngestionStore / SQLite) — never live APIs (the Golden Rule, Step 2e Pillar 1).

Pacing (Pillar 2): strictly sequential — one crew at a time — so we never fire
10 orchestrations at once and blow the LLM token budget. The within-crew burst
(6 async analysts) is the real concurrency, bounded by the 150 s spacing and the
LLM router's 429→fallback chain.

Context (Pillar 3): the latest 'MACRO' and 'VIX' rows are read from SQLite and
formatted by build_desk_context_from_store; news/quote/TA/competitor reach the
agents via the offline tools + the store-built RAG briefing.

Output (Pillar 4): each report is written to the RunStore (trace) AND the
ReportStore (latest-per-ticker, durable) after the output rail + gatekeeper.

Lifecycle (main.py lifespan): start_synthesis_loop / stop_synthesis_loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.engine import build_synthesis_engine
from app.ingestion_store import IngestionStore
from app.macro_context import build_desk_context_from_store
from app.schemas import RagInput, RetrievedDocIn, SynthesizeRequest
from app.watchlist import WATCHLIST_ORDERED, normalize

if TYPE_CHECKING:
    from app.config import Settings
    from app.report_store import ReportStore

log = logging.getLogger(__name__)


# ── context builders ────────────────────────────────────────────────────────

def _build_rag_from_store(store: IngestionStore, ticker: str, news_limit: int) -> RagInput:
    """Assemble a RAG-style briefing from the cache (quote + TA + news) — no live call.

    Bug #2 fix: the structured technical indicators (RSI/MACD/Bollinger) and the
    quote are now surfaced in the briefing so the fundamental analyst and the
    synthesiser see real numbers, not just headlines.
    """
    t = normalize(ticker)
    quote_rows = store.query_latest(t, "quote", 1)
    ta_rows = store.query_latest(t, "ta_signal", 1)
    news_rows = (
        store.query_latest(t, "news", news_limit)
        + store.query_latest(t, "tavily_news", news_limit)
    )
    rows = quote_rows + ta_rows + news_rows

    def _src(r) -> str:
        m = r.meta
        return m.get("publisher") or m.get("url") or r.source_type

    retrieved = [
        RetrievedDocIn(
            id=r.id, title=r.title, source=_src(r),
            published_at=r.published_at, text=r.body[:1000],
        )
        for r in rows
    ]

    lines: list[str] = []
    if quote_rows:
        q = quote_rows[0].meta
        lines.append(
            f"- Quote: price {q.get('price')}, EPS {q.get('eps')}, P/E {q.get('pe')} "
            f"[source: ingested quote]"
        )
    if ta_rows:
        m = ta_rows[0].meta
        lines.append(
            f"- Technicals: RSI {m.get('rsi')} ({m.get('rsi_signal')}), "
            f"MACD {m.get('macd_cross')}, Bollinger {m.get('bb_position')} "
            f"[source: ingested TA]"
        )
    for r in news_rows[:news_limit]:
        # Bug #5: cite the real source (publisher / url) from meta, not the
        # headline as its own source.
        meta = r.meta
        src = meta.get("publisher") or meta.get("url") or r.source_type
        lines.append(f"- {r.title} [source: {src}]")

    if lines:
        summary = (
            f"Latest ingested data for {t}:\n" + "\n".join(lines) + "\n"
            f"Coverage: {len(news_rows)} news, {len(ta_rows)} TA, {len(quote_rows)} quote."
        )
    else:
        summary = "The retrieved context does not cover this (no cached data for this ticker yet)."
    return RagInput(summary=summary, retrieved=retrieved)


def _auto_question(ticker: str, horizon_days: int) -> str:
    return (
        f"Continuous desk monitoring: based strictly on the latest ingested market "
        f"data, what is the {horizon_days}-day outlook and the key risks for "
        f"{normalize(ticker)}, given the current macro/fear regime and peer action?"
    )


# ── single-ticker synthesis (blocking; runs via to_thread) ───────────────────

def synthesize_ticker_offline(
    ticker, cfg, engine, runs, store, *, horizon_days=None, question=None,
):
    """Core OFFLINE synthesis for ONE ticker — the decoupled primitive.

    Reads the ingestion cache (quote/TA/news + macro/VIX), runs the offline crew,
    applies the output rail (degrade-open) + gatekeeper, sets the report on the
    run, and returns it. NO live rag-service / orchestrator dependency. Shared by
    the continuous loop AND the daily briefing (Bug #1 fix). Never raises.
    """
    from app.gatekeeper import enforce as gatekeeper_enforce
    from app.orchestrator import _apply_output_rail
    from app.social_pipeline import get_social_context

    run = runs.start(ticker)
    run.log("synthesis_loop_start", {"ticker": normalize(ticker)})
    try:
        h = horizon_days or cfg.synthesis_horizon_days
        rag = _build_rag_from_store(store, ticker, cfg.synthesis_news_limit)
        macro = build_desk_context_from_store(
            store, ticker, stale_minutes=cfg.synthesis_macro_stale_minutes
        )
        social = get_social_context(ticker)
        # Cached TA → lets the DeterministicEngine tilt without a chart (Bug #3);
        # the CrewEngine ignores it (it pulls TA via get_technical_indicators).
        ta_rows = store.query_latest(normalize(ticker), "ta_signal", 1)
        ta_signal = ta_rows[0].meta if ta_rows else None
        run.log("synthesis_loop_context", {
            "news": len(rag.retrieved), "macro_chars": len(macro),
            "has_social": bool(social), "has_ta": ta_signal is not None,
        })

        sreq = SynthesizeRequest(
            ticker=ticker,
            question=question or _auto_question(ticker, h),
            horizon_days=h,
            rag=rag,
            vision=None,
            macro_context=macro,
            social_context=social,
            ta_signal=ta_signal,
        )
        report = engine.synthesize(sreq, run)
        report, out_reasons = _apply_output_rail(report, rag, run)
        if report is None:
            runs.set_blocked(run.run_id, ["Report flagged for human review"] + out_reasons)
            return None

        gk = gatekeeper_enforce(report, run.run_id)
        report = gk.report
        run.log("gatekeeper", {
            "ticker": report.ticker,
            "execution_allowed": gk.execution_allowed,
            "violations": gk.violation_reasons,
        })

        runs.set_report(run.run_id, report)
        run.log("synthesis_loop_done", {
            "ticker": report.ticker, "p_bull": report.probabilities.bullish,
        })
        return report
    except Exception as exc:  # noqa: BLE001 — must survive any single failure
        log.exception("synthesis: offline synthesis failed ticker=%s: %s", ticker, exc)
        runs.set_error(run.run_id, f"{type(exc).__name__}: {exc}")
        return None


def _synthesize_one(ticker, cfg, engine, runs, store, report_store):
    """Continuous-loop wrapper: synthesise offline, then persist to the ReportStore."""
    from app.offline_tools import offline_macro_snapshot, offline_vix_curve

    report = synthesize_ticker_offline(ticker, cfg, engine, runs, store)
    if report is None:
        report_store.record_heartbeat(normalize(ticker), "error")
        return None
    macro_struct = {"macro": offline_macro_snapshot(store), "vix": offline_vix_curve(store)}
    report_store.save(report.ticker, report.model_dump(), report.run_id, macro=macro_struct)
    report_store.record_heartbeat(report.ticker, "done")
    return report


# ── background loop ──────────────────────────────────────────────────────────

async def _synthesis_loop(cfg: "Settings", runs, report_store: "ReportStore") -> None:
    store = IngestionStore(cfg.ingestion_db_path)
    engine = build_synthesis_engine(store)
    tickers = list(WATCHLIST_ORDERED)
    log.info(
        "synthesis-loop: started (interval=%ds, tickers=%d, engine=%s, skip_unchanged=%s)",
        cfg.synthesis_interval_s, len(tickers), engine.name, cfg.synthesis_skip_unchanged,
    )
    idx = report_store.get_cursor()
    while True:
        ticker = tickers[idx % len(tickers)]
        try:
            proceed = True
            if cfg.synthesis_skip_unchanged:
                rows = await asyncio.to_thread(store.query_latest, ticker, None, 1)
                latest = rows[0].ingested_at if rows else None
                if latest is None or latest == report_store.last_seen(ticker):
                    proceed = False
                    log.info("synthesis-loop: %s has no new data -- skipping", ticker)
            if proceed:
                await asyncio.to_thread(
                    _synthesize_one, ticker, cfg, engine, runs, store, report_store
                )
                rows = await asyncio.to_thread(store.query_latest, ticker, None, 1)
                if rows:
                    report_store.mark_seen(ticker, rows[0].ingested_at)
        except asyncio.CancelledError:
            log.info("synthesis-loop: cancelled -- shutting down")
            store.close()
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("synthesis-loop: unhandled error on %s: %s", ticker, exc)

        idx = (idx + 1) % len(tickers)
        report_store.set_cursor(idx)
        try:
            await asyncio.sleep(cfg.synthesis_interval_s)
        except asyncio.CancelledError:
            log.info("synthesis-loop: cancelled during sleep")
            store.close()
            return


def start_synthesis_loop(cfg: "Settings", runs, report_store: "ReportStore") -> asyncio.Task:
    return asyncio.create_task(_synthesis_loop(cfg, runs, report_store), name="synthesis-loop")


def stop_synthesis_loop(task: asyncio.Task) -> None:
    if task and not task.done():
        task.cancel()
        log.info("synthesis-loop: stop requested")
