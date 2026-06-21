"""1-Minute Continuous Ingestion Engine (Step 2d).

A lightweight background scheduler that runs every ``INGESTION_INTERVAL_S``
seconds (default 60). It fetches News, Technical Analysis signals, Macro
snapshots, Competitor prices, and (on a slower cadence) Tavily-enriched news
for all 10 watchlist tickers, then persists every data point to:

  1. **SQLite** (``IngestionStore``) — structured time-ordered queries for the
     synthesis loop (Step 2e).
  2. **RAG /ingest** — vector embeddings for CrewAI agent retrieval.

Design constraints
──────────────────
• **No LLM calls** — pure data fetching + cheap heuristic TA computation.
• **Rate-limit safe** — ``asyncio.Semaphore`` caps concurrent yfinance calls;
  Tavily runs at most once every ``TAVILY_INTERVAL_S`` (default 1800 s = 30 min).
• **Decoupled** — the synthesis loop reads from SQLite, never from live APIs.
• **Never raises** — every fetcher returns ``list[IngestionRow]`` or ``[]``.

Lifecycle (called from ``main.py`` lifespan)
────────────────────────────────────────────
  ``start_ingestion_engine(cfg) -> asyncio.Task``
  ``stop_ingestion_engine(task) -> None``
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import requests

from app.ingestion_store import IngestionRow, IngestionStore
from app.watchlist import WATCHLIST_ORDERED, competitors_for, normalize
# TA math lives in one place (app.ta_indicators); re-export the historical
# private names so existing tests (_compute_rsi/_compute_macd/_compute_bollinger)
# keep working.
from app.ta_indicators import (  # noqa: F401
    _ema,
    compute_bollinger as _compute_bollinger,
    compute_indicators,
    compute_macd as _compute_macd,
    compute_rsi as _compute_rsi,
)

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

_HTTP_TIMEOUT = 10

# Tavily slow-cadence: only query once every this many seconds (per ticker).
TAVILY_INTERVAL_S = 1800  # 30 minutes

# Track the last Tavily fetch time per ticker.
_tavily_last: dict[str, float] = {}


# ── lifecycle ─────────────────────────────────────────────────────────────────

def start_ingestion_engine(cfg: "Settings") -> asyncio.Task:
    """Schedule the background ingestion loop and return the Task."""
    task = asyncio.create_task(
        _ingestion_loop(cfg),
        name="ingestion-engine",
    )
    log.info(
        "ingestion-engine: started (interval=%ds, concurrency=%d, tavily=%s)",
        cfg.ingestion_interval_s,
        cfg.ingestion_concurrency,
        "enabled" if cfg.tavily_api_key.get_secret_value() else "disabled",
    )
    return task


def stop_ingestion_engine(task: asyncio.Task) -> None:
    """Cancel the background ingestion loop."""
    if task and not task.done():
        task.cancel()
        log.info("ingestion-engine: stop requested")


# ── background loop ──────────────────────────────────────────────────────────

async def _ingestion_loop(cfg: "Settings") -> None:
    """Run one ingestion cycle then sleep; repeat forever until cancelled."""
    store = IngestionStore(cfg.ingestion_db_path)
    while True:
        cycle_start = time.monotonic()
        try:
            await _run_ingestion_cycle(cfg, store)
        except asyncio.CancelledError:
            log.info("ingestion-engine: cancelled -- shutting down")
            store.close()
            return
        except Exception as exc:
            log.exception("ingestion-engine: unhandled error in cycle: %s", exc)

        elapsed = time.monotonic() - cycle_start
        sleep_s = max(0, cfg.ingestion_interval_s - elapsed)
        if sleep_s == 0:
            log.warning(
                "ingestion-engine: cycle took %.1fs (> %ds interval) -- skipping sleep",
                elapsed, cfg.ingestion_interval_s,
            )
        else:
            log.info(
                "ingestion-engine: cycle done in %.1fs, sleeping %.0fs",
                elapsed, sleep_s,
            )
        try:
            await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            log.info("ingestion-engine: cancelled during sleep")
            store.close()
            return


# ── core cycle ────────────────────────────────────────────────────────────────

async def _run_ingestion_cycle(cfg: "Settings", store: IngestionStore) -> None:
    """One full 60-second ingestion cycle across all 10 tickers."""
    sem = asyncio.Semaphore(cfg.ingestion_concurrency)
    all_rows: list[IngestionRow] = []

    # ── macro (shared across all tickers, cached 60s) ─────────────────────
    macro_rows = await asyncio.to_thread(_ingest_macro)
    all_rows.extend(macro_rows)

    # ── per-ticker: news + TA + competitors (+ Tavily on slow cadence) ────
    async def _process_ticker(ticker: str) -> list[IngestionRow]:
        rows: list[IngestionRow] = []
        async with sem:
            # Primary quote + fundamentals (EPS/PE/market cap) — powers the
            # offline get_market_quote tool in the synthesis loop (Step 2e A1).
            quote = await asyncio.to_thread(_ingest_quote, ticker)
            rows.extend(quote)

            # News (yfinance)
            news = await asyncio.to_thread(_ingest_news, ticker)
            rows.extend(news)

            # Technical Analysis signals
            ta = await asyncio.to_thread(_ingest_ta_signals, ticker)
            rows.extend(ta)

            # Competitor prices
            comp = await asyncio.to_thread(_ingest_competitors, ticker)
            rows.extend(comp)

            # Tavily enriched news (slow cadence)
            tavily_key = cfg.tavily_api_key.get_secret_value()
            if tavily_key:
                now = time.monotonic()
                last = _tavily_last.get(ticker, 0.0)
                if now - last >= cfg.ingestion_tavily_interval_s:
                    tav = await asyncio.to_thread(_ingest_tavily_news, ticker, tavily_key)
                    rows.extend(tav)
                    _tavily_last[ticker] = now

        return rows

    tasks = [_process_ticker(t) for t in WATCHLIST_ORDERED]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.warning(
                "ingestion-engine: ticker %s failed: %s",
                WATCHLIST_ORDERED[i], result,
            )
        else:
            all_rows.extend(result)

    # ── persist to SQLite ─────────────────────────────────────────────────
    new_count = await asyncio.to_thread(store.upsert, all_rows)
    total = await asyncio.to_thread(store.count)
    log.info(
        "ingestion-engine: cycle total_fetched=%d new_inserted=%d db_total=%d",
        len(all_rows), new_count, total,
    )

    # ── push new rows to RAG /ingest ──────────────────────────────────────
    if new_count > 0:
        asyncio.create_task(_push_to_rag(all_rows, cfg.rag_url))

    # ── prune old data ────────────────────────────────────────────────────
    await asyncio.to_thread(store.prune, cfg.ingestion_prune_hours)


# ── fetchers (all synchronous, called via to_thread) ──────────────────────────

def _ingest_quote(ticker: str) -> list[IngestionRow]:
    """Fetch the primary ticker's own quote + fundamentals (EPS/PE/market cap).

    Uses the resilient market-data chain (Polygon → Alpaca → yfinance). Stored
    as source_type='quote' so the synthesis loop's offline get_market_quote is
    fully populated without any live call (Step 2e, decision A1).
    """
    from app.finance_tools import fetch_quote

    try:
        q = fetch_quote(ticker)
        if not q or "error" in q:
            return []
    except Exception as exc:
        log.warning("ingestion quote(%s) failed: %s", ticker, exc)
        return []

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [IngestionRow(
        ticker=normalize(ticker),
        source_type="quote",
        title=f"Quote: {normalize(ticker)} @ {now_iso}",
        body=json.dumps(q, indent=2, default=str),
        published_at=now_iso,
        meta_json=json.dumps(q, default=str),
    )]


def _ingest_news(ticker: str) -> list[IngestionRow]:
    """Fetch news headlines from yfinance for a ticker."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        news = tk.news or []
    except Exception as exc:
        log.warning("ingestion news(%s) failed: %s", ticker, exc)
        return []

    rows: list[IngestionRow] = []
    for item in news:
        title = item.get("title", "")
        if not title:
            continue
        publisher = item.get("publisher", "yfinance")
        link = item.get("link", "")
        pub_time = item.get("providerPublishTime", 0)
        if pub_time:
            published_at = datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat(timespec="seconds")
        else:
            published_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        body = f"News: {title}\nLink: {link}\nPublisher: {publisher}"
        rows.append(IngestionRow(
            ticker=normalize(ticker),
            source_type="news",
            title=title,
            body=body,
            published_at=published_at,
            meta_json=json.dumps({"publisher": publisher, "link": link}),
        ))
    return rows


def _ingest_ta_signals(ticker: str) -> list[IngestionRow]:
    """Compute RSI(14), MACD(12,26,9), Bollinger Band position from 5m bars.

    Pure Python — no TA-lib dependency. Returns a single IngestionRow with
    the computed indicators in meta_json.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="1d", interval="5m", prepost=False)
        if hist is None or len(hist) < 15:
            return []
        closes = hist["Close"].dropna().tolist()
        if len(closes) < 15:
            return []
    except Exception as exc:
        log.warning("ingestion ta_signals(%s) failed: %s", ticker, exc)
        return []

    indicators = compute_indicators(closes)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    title = f"TA Signals: {normalize(ticker)} @ {now_iso}"
    body_parts = []
    for k, v in indicators.items():
        body_parts.append(f"{k}: {v}")
    body = f"Technical Analysis for {normalize(ticker)}\n" + "\n".join(body_parts)

    return [IngestionRow(
        ticker=normalize(ticker),
        source_type="ta_signal",
        title=title,
        body=body,
        published_at=now_iso,
        meta_json=json.dumps(indicators),
    )]


def _ingest_macro() -> list[IngestionRow]:
    """Fetch broad-market macro snapshot + VIX curve (market-wide, shared)."""
    from app.finance_tools import fetch_macro_snapshot, fetch_vix_curve

    rows: list[IngestionRow] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Macro (S&P 500 / NASDAQ)
    try:
        macro = fetch_macro_snapshot()
        if "error" not in macro:
            body = json.dumps(macro, indent=2)
            rows.append(IngestionRow(
                ticker="MACRO",
                source_type="macro",
                title=f"Broad Market Snapshot @ {now_iso}",
                body=body,
                published_at=now_iso,
                meta_json=json.dumps(macro),
            ))
    except Exception as exc:
        log.warning("ingestion macro snapshot failed: %s", exc)

    # VIX curve
    try:
        vix = fetch_vix_curve()
        if "error" not in vix:
            body = json.dumps(vix, indent=2)
            rows.append(IngestionRow(
                ticker="VIX",
                source_type="macro",
                title=f"VIX Term Structure @ {now_iso}",
                body=body,
                published_at=now_iso,
                meta_json=json.dumps(vix),
            ))
    except Exception as exc:
        log.warning("ingestion VIX curve failed: %s", exc)

    return rows


def _ingest_competitors(ticker: str) -> list[IngestionRow]:
    """Fetch competitor/peer prices for relative-strength context."""
    from app.finance_tools import fetch_competitors

    try:
        data = fetch_competitors(ticker)
        if "error" in data or not data.get("peers"):
            return []
    except Exception as exc:
        log.warning("ingestion competitors(%s) failed: %s", ticker, exc)
        return []

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    peers = data["peers"]
    body_lines = [f"Competitor read-through for {normalize(ticker)}:"]
    for p in peers:
        chg = f"{p['change_pct']:+.2f}%" if p.get("change_pct") is not None else "n/a"
        body_lines.append(f"  {p['ticker']}: ${p.get('price', 'n/a')} ({chg})")

    return [IngestionRow(
        ticker=normalize(ticker),
        source_type="competitor",
        title=f"Peers: {normalize(ticker)} @ {now_iso}",
        body="\n".join(body_lines),
        published_at=now_iso,
        meta_json=json.dumps(data),
    )]


def _ingest_tavily_news(ticker: str, api_key: str) -> list[IngestionRow]:
    """Fetch enriched financial news from Tavily Search API.

    Called at most once every TAVILY_INTERVAL_S per ticker to control API costs.
    """
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": f"{ticker} stock market news analysis",
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
            },
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code == 429:
            log.warning("Tavily rate-limited for %s -- will retry next interval", ticker)
            return []
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        log.warning("ingestion tavily_news(%s) failed: %s", ticker, exc)
        return []

    rows: list[IngestionRow] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in results:
        title = item.get("title", "")
        content = item.get("content", "")
        url = item.get("url", "")
        if not title:
            continue
        body = f"News: {title}\n{content}\nSource: {url}"
        rows.append(IngestionRow(
            ticker=normalize(ticker),
            source_type="tavily_news",
            title=title,
            body=body,
            published_at=item.get("published_date", now_iso),
            meta_json=json.dumps({"url": url, "score": item.get("score", 0)}),
        ))
    return rows


# ── push to RAG ───────────────────────────────────────────────────────────────

async def _push_to_rag(rows: list[IngestionRow], rag_url: str) -> None:
    """Batch-POST ingested data to the RAG /ingest endpoint."""
    documents = []
    for r in rows:
        documents.append({
            "ticker": r.ticker,
            "source": r.source_type,
            "title": r.title,
            "text": r.body,
            "published_at": r.published_at,
        })
    if not documents:
        return
    try:
        await asyncio.to_thread(
            requests.post,
            f"{rag_url}/ingest",
            json={"documents": documents},
            timeout=15,
        )
        log.debug("ingestion-engine: pushed %d documents to RAG", len(documents))
    except Exception as exc:
        log.warning("ingestion-engine: RAG push failed: %s", exc)


# TA indicator math now lives in app.ta_indicators (imported at the top and
# re-exported above as _compute_rsi / _compute_macd / _compute_bollinger).
