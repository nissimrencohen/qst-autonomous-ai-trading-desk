"""Multi-source market data layer.

Provider priority (configured by AGENTIC_MARKET_DATA_CHAIN):
  1. polygon  — Polygon.io REST API (requires AGENTIC_POLYGON_API_KEY)
  2. alpaca   — Alpaca Market Data API (requires AGENTIC_ALPACA_KEY)
  3. yfinance — free fallback, no key needed

`fetch_quote_resilient(ticker)` returns the same dict shape as the
original `finance_tools.fetch_quote()` so all callers are transparent to
the provider change.

The finance_tools.py build_finance_tools() wraps this function so the
CrewAI agents automatically benefit from redundancy.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 8


# ── Polygon.io ────────────────────────────────────────────────────────────────

def _fetch_polygon(ticker: str) -> dict[str, Any] | None:
    key = settings.polygon_api_key.get_secret_value()
    if not key:
        return None
    try:
        import requests
        resp = requests.get(
            f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}",
            params={"apiKey": key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 403:
            log.warning("polygon: API key invalid or plan doesn't cover %s", ticker)
            return None
        resp.raise_for_status()
        data = resp.json()
        day = (data.get("ticker") or {}).get("day") or {}
        last = (data.get("ticker") or {}).get("lastTrade") or {}
        price = last.get("p") or day.get("c")
        if price is None:
            return None
        return {
            "ticker": ticker.upper(),
            "name": None,
            "price": float(price),
            "currency": "USD",
            "eps": None,
            "pe": None,
            "market_cap": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "_source": "polygon",
        }
    except Exception as exc:
        log.warning("polygon fetch_quote(%s) failed: %s", ticker, exc)
        return None


# ── Alpaca Market Data ────────────────────────────────────────────────────────

def _fetch_alpaca(ticker: str) -> dict[str, Any] | None:
    key = settings.alpaca_key.get_secret_value()
    secret = settings.alpaca_secret.get_secret_value()
    if not key or not secret:
        return None
    try:
        import requests
        resp = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker.upper()}/trades/latest",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            log.warning("alpaca market data: auth failed for %s", ticker)
            return None
        resp.raise_for_status()
        trade = resp.json().get("trade") or {}
        price = trade.get("p")
        if price is None:
            return None
        return {
            "ticker": ticker.upper(),
            "name": None,
            "price": float(price),
            "currency": "USD",
            "eps": None,
            "pe": None,
            "market_cap": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "_source": "alpaca",
        }
    except Exception as exc:
        log.warning("alpaca fetch_quote(%s) failed: %s", ticker, exc)
        return None


# ── yfinance fallback ─────────────────────────────────────────────────────────

def _fetch_yfinance(ticker: str) -> dict[str, Any] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        info = yf.Ticker(ticker).info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price is None:
            return None
        return {
            "ticker": ticker.upper(),
            "name": info.get("longName") or info.get("shortName"),
            "price": price,
            "currency": info.get("currency"),
            "eps": info.get("trailingEps"),
            "pe": info.get("trailingPE") or info.get("forwardPE"),
            "market_cap": info.get("marketCap"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "_source": "yfinance",
        }
    except Exception as exc:
        log.warning("yfinance fetch_quote(%s) failed: %s", ticker, exc)
        return None


# ── public entrypoint ─────────────────────────────────────────────────────────

_PROVIDERS = {
    "polygon":  _fetch_polygon,
    "alpaca":   _fetch_alpaca,
    "yfinance": _fetch_yfinance,
}


def fetch_quote_resilient(ticker: str) -> dict[str, Any]:
    """Try providers in AGENTIC_MARKET_DATA_CHAIN order, return first success."""
    chain = [p.strip() for p in settings.market_data_chain.split(",") if p.strip()]
    for provider in chain:
        fn = _PROVIDERS.get(provider)
        if fn is None:
            log.warning("unknown market data provider: %s", provider)
            continue
        result = fn(ticker)
        if result is not None:
            log.debug("market_data: %s served quote for %s", provider, ticker)
            return result

    return {"error": f"all market data providers failed for {ticker}"}
