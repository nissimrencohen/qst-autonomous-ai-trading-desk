"""Offline, store-backed CrewAI tools for the Continuous Synthesis Loop (Step 2e).

The Golden Rule: in continuous mode the agents read EXCLUSIVELY from the
``IngestionStore`` (the 1-minute ingestion engine's SQLite cache). These tools
mirror the *names* of the live tools in ``finance_tools.py`` so the Step-2f
agent prompts work unchanged — but every lookup resolves from cached rows, so
there is NOT a single live ``yfinance`` / News / web call during synthesis.

This module deliberately imports neither ``yfinance`` nor ``requests`` nor
``app.finance_tools`` — the decoupling is structural, and a unit test asserts it.

Source-type mapping (see ingestion_engine.py):
  get_market_quote        → 'quote'      (price + EPS/PE/market cap)
  get_technical_indicators→ 'ta_signal'  (RSI / MACD / Bollinger)
  get_competitor_analysis → 'competitor' (peer prices)
  get_vix_curve           → 'macro' under ticker 'VIX'
  get_macro_snapshot      → 'macro' under ticker 'MACRO'
  get_options_sentiment / get_spacex_launch_schedule → not ingested → explicit
  "unavailable in continuous mode" (NO live fallback).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.watchlist import normalize

log = logging.getLogger(__name__)


def _latest_meta(store, ticker: str, source_type: str) -> dict[str, Any] | None:
    """Return the meta dict of the most recent row, or None if absent."""
    try:
        rows = store.query_latest(ticker, source_type, 1)
    except Exception as exc:  # pragma: no cover - store should never raise
        log.warning("offline tool store read failed (%s/%s): %s", ticker, source_type, exc)
        return None
    return rows[0].meta if rows else None


# ── pure store-readers (directly unit-testable; the @tool wrappers serialise these) ──

def offline_market_quote(store, ticker: str) -> dict[str, Any]:
    t = normalize(ticker)
    meta = _latest_meta(store, t, "quote")
    if meta:
        return meta
    # Graceful fallback: TA rows carry the last 5-minute close price.
    ta = _latest_meta(store, t, "ta_signal")
    if ta and ta.get("price") is not None:
        return {"ticker": t, "price": ta["price"],
                "note": "price from cached TA snapshot; fundamentals not in cache this cycle"}
    return {"error": f"no cached quote for {t} (continuous mode)"}


def offline_technical_indicators(store, ticker: str) -> dict[str, Any]:
    t = normalize(ticker)
    return _latest_meta(store, t, "ta_signal") or {"error": f"no cached TA for {t} (continuous mode)"}


def offline_vix_curve(store) -> dict[str, Any]:
    return _latest_meta(store, "VIX", "macro") or {"error": "no cached VIX data (continuous mode)"}


def offline_macro_snapshot(store) -> dict[str, Any]:
    return _latest_meta(store, "MACRO", "macro") or {"error": "no cached macro data (continuous mode)"}


def offline_competitor_analysis(store, ticker: str) -> dict[str, Any]:
    t = normalize(ticker)
    return _latest_meta(store, t, "competitor") or {
        "ticker": t, "peers": [], "note": "no cached competitor data (continuous mode)"}


_OPTIONS_UNAVAILABLE = {"error": "options sentiment not available in continuous mode (not ingested)"}
_LAUNCH_UNAVAILABLE = {"error": "launch schedule not available in continuous mode (not ingested)"}


def build_offline_finance_tools(store) -> list:
    """Wrap the store-readers as CrewAI tools. Returns [] if crewai is unavailable."""
    try:
        from crewai.tools import tool
    except ImportError:  # pragma: no cover
        log.warning("crewai.tools unavailable — offline finance tools disabled")
        return []

    @tool("get_market_quote")
    def get_market_quote(ticker: str) -> str:
        """Live price and fundamentals (EPS, P/E, market cap) for a ticker, served
        from the desk's ingested cache (no live call)."""
        return json.dumps(offline_market_quote(store, ticker), default=str)

    @tool("get_technical_indicators")
    def get_technical_indicators(ticker: str) -> str:
        """Cached technical indicators (RSI, MACD, Bollinger Band position) for a
        ticker, computed by the ingestion engine from 5-minute bars."""
        return json.dumps(offline_technical_indicators(store, ticker), default=str)

    @tool("get_vix_curve")
    def get_vix_curve(symbol: str = "^VIX") -> str:
        """VIX term structure (9D/30D/3M), contango/backwardation, and fear regime
        from the desk's ingested cache. Market-wide; `symbol` may be left default."""
        return json.dumps(offline_vix_curve(store), default=str)

    @tool("get_macro_snapshot")
    def get_macro_snapshot(scope: str = "broad") -> str:
        """Broad-market macro backdrop (S&P 500 / NASDAQ level, change, tone) from
        the desk's ingested cache. Market-wide; `scope` may be left default."""
        return json.dumps(offline_macro_snapshot(store), default=str)

    @tool("get_competitor_analysis")
    def get_competitor_analysis(ticker: str) -> str:
        """Competitor/peer read-through (each peer's price + daily % change) for a
        ticker, served from the desk's ingested cache."""
        return json.dumps(offline_competitor_analysis(store, ticker), default=str)

    @tool("get_options_sentiment")
    def get_options_sentiment(ticker: str) -> str:
        """Options positioning. NOT ingested into the cache — unavailable in the
        continuous/offline desk; state the data gap rather than guessing."""
        return json.dumps(_OPTIONS_UNAVAILABLE)

    @tool("get_spacex_launch_schedule")
    def get_spacex_launch_schedule(limit: str = "5") -> str:
        """SpaceX launch cadence. NOT ingested into the cache — unavailable in the
        continuous/offline desk; state the data gap rather than guessing."""
        return json.dumps(_LAUNCH_UNAVAILABLE)

    return [
        get_market_quote, get_technical_indicators, get_vix_curve,
        get_macro_snapshot, get_competitor_analysis, get_options_sentiment,
        get_spacex_launch_schedule,
    ]
