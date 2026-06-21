"""MCP market-data backing functions — single source of truth.

These pure functions are shared by:
  • app/mcp_server.py    — the Model Context Protocol server (FastMCP), which
                           exposes them as standards-compliant MCP tools to any
                           MCP client (Claude Desktop, IDEs, CrewAI adapter…).
  • app/mcp_tools.py     — in-process CrewAI tool wrappers used by the desk's
                           synthesis crew.

Both layers therefore return byte-for-byte identical payloads, so the protocol
path and the in-process path can never drift.

No crewai / mcp imports here — this module stays dependency-light and unit-
testable. Live data comes from the existing resilient market-data layer
(app.market_data → polygon/alpaca/yfinance) and the shared TA math
(app.ta_indicators), so MCP results agree exactly with the rest of the desk.

Every function is rate-limit resilient: on any provider error it returns
{"ticker": ..., "error": ...} and NEVER raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_market_cap(v: float | int | None) -> str | None:
    """Human-readable market cap (e.g. 3.21T, 845.0B, 12.3M)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= scale:
            return f"{v / scale:.2f}{unit}"
    return f"{v:.0f}"


def get_technical_data(ticker: str) -> dict[str, Any]:
    """Technical snapshot for a ticker (MCP tool `get_technical_data`).

    Returns current price, intraday volume, daily % change, RSI(14),
    MACD(12/26/9) with cross direction, and Bollinger(20,2σ) band position.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return {"ticker": ticker, "error": "empty ticker"}

    # RSI / MACD / Bollinger from the shared TA math (same as ingestion cache).
    from app.finance_tools import fetch_technical_indicators

    ind = fetch_technical_indicators(t)
    if "error" in ind:
        return {"ticker": t, "error": ind["error"], "as_of": _now_iso()}

    # Current price / previous close / volume from yfinance fast_info.
    price = ind.get("price")
    volume: int | None = None
    change_pct: float | None = None
    try:
        import yfinance as yf

        fi = yf.Ticker(t).fast_info
        last = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        vol = getattr(fi, "last_volume", None)
        if last is not None:
            price = round(float(last), 2)
        if vol is not None:
            volume = int(vol)
        if last is not None and prev:
            change_pct = round((float(last) - float(prev)) / float(prev) * 100, 2)
    except Exception as exc:  # noqa: BLE001 — resilience over precision
        log.warning("get_technical_data(%s): fast_info leg failed: %s", t, exc)

    return {
        "ticker": t,
        "price": price,
        "volume": volume,
        "change_pct": change_pct,
        "rsi": ind.get("rsi"),
        "rsi_signal": ind.get("rsi_signal"),
        "macd": ind.get("macd"),
        "macd_signal": ind.get("macd_signal"),
        "macd_histogram": ind.get("macd_histogram"),
        "macd_cross": ind.get("macd_cross"),
        "bb_upper": ind.get("bb_upper"),
        "bb_mid": ind.get("bb_mid"),
        "bb_lower": ind.get("bb_lower"),
        "bb_position": ind.get("bb_position"),
        "as_of": _now_iso(),
        "source": "mcp:qst-market-data",
    }


def get_fundamental_data(ticker: str) -> dict[str, Any]:
    """Fundamental snapshot for a ticker (MCP tool `get_fundamental_data`).

    Returns P/E ratio, trailing EPS, market cap, company name, current price,
    52-week range and currency. Sourced via the resilient provider chain
    (polygon → alpaca → yfinance).
    """
    t = (ticker or "").strip().upper()
    if not t:
        return {"ticker": ticker, "error": "empty ticker"}

    from app.market_data import fetch_quote_resilient

    q = fetch_quote_resilient(t)
    if "error" in q and q.get("price") is None:
        return {"ticker": t, "error": q.get("error", "quote unavailable"), "as_of": _now_iso()}

    return {
        "ticker": t,
        "name": q.get("name"),
        "price": q.get("price"),
        "pe": q.get("pe"),
        "eps": q.get("eps"),
        "market_cap": q.get("market_cap"),
        "market_cap_fmt": _fmt_market_cap(q.get("market_cap")),
        "fifty_two_week_high": q.get("fifty_two_week_high"),
        "fifty_two_week_low": q.get("fifty_two_week_low"),
        "currency": q.get("currency", "USD"),
        "as_of": _now_iso(),
        "source": f"mcp:qst-market-data ({q.get('_source', 'yfinance')})",
    }
