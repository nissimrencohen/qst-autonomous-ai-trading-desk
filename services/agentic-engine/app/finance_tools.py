"""Market-data tools for the CrewAI trading desk.

Two layers, mirroring web_tools.py:
  • pure functions (fetch_*) — no crewai dependency, unit-testable, return dicts
  • build_finance_tools() — wraps them as crewai @tool objects for the agents

Data sources (all free, no key required except optional Finnhub):
  • yfinance        — live quotes, fundamentals (EPS/PE), option chains, VIX family,
                      broad-market indices (S&P 500 / NASDAQ), competitor quotes
  • thespacedevs    — public Launch Library 2 API for SpaceX launch cadence

Mandatory macro & fear context (mission Req 2): fetch_macro_snapshot() (broad
market) and fetch_vix_curve() (fear index) are composed by app.macro_context
into a block injected into EVERY single-ticker analysis. fetch_competitors()
provides the peer read-through (Req 3). All fetchers are rate-limit resilient:
on any provider error they return {"error": ...} and never raise.

Honesty note: real institutional dark-pool prints and dealer gamma exposure are
NOT available through free feeds. get_options_sentiment() derives put/call and
IV-skew signals from the public yfinance option chain and labels any gamma read
as an *approximation from public data*, never a live dark-pool feed.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# VIX term-structure constituents (front → back). All are CBOE indices on Yahoo.
_VIX_FRONT = "^VIX"      # 30-day implied vol
_VIX_BACK = "^VIX3M"     # 3-month implied vol
_VIX_SHORT = "^VIX9D"    # 9-day implied vol

_LAUNCH_API = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
_HTTP_TIMEOUT = 8


# ── pure data functions ─────────────────────────────────────────────────────

def fetch_quote(ticker: str) -> dict[str, Any]:
    """Live quote + fundamentals — multi-source with yfinance fallback (v1.4).

    Tries providers in AGENTIC_MARKET_DATA_CHAIN order (polygon → alpaca → yfinance).
    Returns {price, eps, pe, market_cap, name, currency} or {error: ...}.
    """
    from app.market_data import fetch_quote_resilient
    return fetch_quote_resilient(ticker)


def fetch_vix_curve() -> dict[str, Any]:
    """VIX term structure (9D / 30D / 3M) → contango vs backwardation + regime."""
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    def _last(sym: str) -> float | None:
        try:
            fi = yf.Ticker(sym).fast_info
            v = getattr(fi, "last_price", None)
            return float(v) if v is not None else None
        except Exception as exc:
            log.warning("VIX leg %s failed: %s", sym, exc)
            return None

    short, front, back = _last(_VIX_SHORT), _last(_VIX_FRONT), _last(_VIX_BACK)
    if front is None or back is None:
        return {"error": "VIX term-structure data unavailable"}

    # contango: longer-dated > front (normal/calm); backwardation: front > back (stress)
    spread = back - front
    if abs(spread) < 0.25:
        structure = "flat"
    elif spread > 0:
        structure = "contango"
    else:
        structure = "backwardation"

    if front < 15:
        regime = "calm"
    elif front < 20:
        regime = "elevated"
    elif front < 30:
        regime = "stress"
    else:
        regime = "panic"

    return {
        "vix_9d": short,
        "vix_30d": front,
        "vix_3m": back,
        "front_back_spread": round(spread, 2),
        "term_structure": structure,
        "regime": regime,
        "note": (
            "Backwardation (front>back) signals acute near-term fear; "
            "steep contango is the calm-market norm."
        ),
    }


def fetch_options_sentiment(ticker: str) -> dict[str, Any]:
    """Put/call ratio + ATM IV skew from the public yfinance option chain.

    Gamma/dark-pool reads here are APPROXIMATIONS from public open interest,
    not a live institutional feed.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            return {"error": f"no listed options for {ticker}"}
        chain = tk.option_chain(expiries[0])
        calls, puts = chain.calls, chain.puts
    except Exception as exc:
        log.warning("fetch_options_sentiment(%s) failed: %s", ticker, exc)
        return {"error": f"option chain lookup failed for {ticker}: {exc}"}

    call_oi = float(calls["openInterest"].fillna(0).sum())
    put_oi = float(puts["openInterest"].fillna(0).sum())
    call_vol = float(calls["volume"].fillna(0).sum())
    put_vol = float(puts["volume"].fillna(0).sum())

    pc_oi = round(put_oi / call_oi, 3) if call_oi else None
    pc_vol = round(put_vol / call_vol, 3) if call_vol else None

    # ATM IV skew: median put IV minus median call IV (positive = downside demand)
    try:
        call_iv = float(calls["impliedVolatility"].median())
        put_iv = float(puts["impliedVolatility"].median())
        iv_skew = round(put_iv - call_iv, 4)
    except Exception:
        iv_skew = None

    if pc_oi is None:
        bias = "unknown"
    elif pc_oi > 1.2:
        bias = "bearish (heavy put positioning)"
    elif pc_oi < 0.7:
        bias = "bullish (heavy call positioning)"
    else:
        bias = "balanced"

    return {
        "ticker": ticker.upper(),
        "expiry": expiries[0],
        "put_call_oi_ratio": pc_oi,
        "put_call_volume_ratio": pc_vol,
        "atm_iv_skew": iv_skew,
        "positioning_bias": bias,
        "note": (
            "Derived from public open interest / volume. Gamma and dark-pool "
            "estimates are approximations, NOT a live institutional feed."
        ),
    }


def fetch_launch_schedule(limit: int = 5) -> dict[str, Any]:
    """Upcoming SpaceX launches via the public thespacedevs Launch Library API."""
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    try:
        resp = requests.get(
            _LAUNCH_API,
            params={"search": "SpaceX", "limit": limit, "mode": "list"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        log.warning("fetch_launch_schedule failed: %s", exc)
        return {"error": f"launch schedule unavailable: {exc}"}

    launches = [
        {
            "name": r.get("name"),
            "net": r.get("net"),  # no-earlier-than timestamp
            "status": (r.get("status") or {}).get("name"),
        }
        for r in results
    ]
    return {"source": "thespacedevs Launch Library 2", "upcoming": launches}


# ── broad-market macro snapshot (mission Req 2) ──────────────────────────────

_SP500 = "^GSPC"        # S&P 500 index   (ETF fallback: SPY)
_NASDAQ = "^IXIC"       # NASDAQ Composite (ETF fallback: QQQ)
_SP500_ETF = "SPY"
_NASDAQ_ETF = "QQQ"


def fetch_macro_snapshot() -> dict[str, Any]:
    """Broad-market backdrop: S&P 500 + NASDAQ level, daily % change, and tone.

    Tries the index symbol first, then its liquid ETF proxy. Resilient: returns
    {"error": ...} when both legs are unavailable; never raises.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    def _leg(primary: str, fallback: str) -> dict | None:
        for sym in (primary, fallback):
            try:
                fi = yf.Ticker(sym).fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                if last and prev:
                    return {
                        "symbol": sym,
                        "price": round(float(last), 2),
                        "change_pct": round((float(last) - float(prev)) / float(prev) * 100, 2),
                    }
            except Exception as exc:
                log.warning("macro leg %s failed: %s", sym, exc)
        return None

    sp = _leg(_SP500, _SP500_ETF)
    ndx = _leg(_NASDAQ, _NASDAQ_ETF)
    if sp is None and ndx is None:
        return {"error": "broad-market data unavailable"}

    changes = [x["change_pct"] for x in (sp, ndx) if x]
    avg = round(sum(changes) / len(changes), 2) if changes else 0.0
    if avg > 0.75:
        tone = "risk-on (broad rally)"
    elif avg > 0.10:
        tone = "mildly positive"
    elif avg >= -0.10:
        tone = "flat / mixed"
    elif avg >= -0.75:
        tone = "mildly negative"
    else:
        tone = "risk-off (broad selloff)"

    return {
        "sp500": sp,
        "nasdaq": ndx,
        "avg_change_pct": avg,
        "market_tone": tone,
        "note": "Broad-market backdrop from index/ETF proxies. Use as the macro regime for every ticker.",
    }


def fetch_competitors(ticker: str, limit: int = 4) -> dict[str, Any]:
    """Peer/competitor read-through for a watchlist instrument (mission Req 3).

    Returns each peer's live price + daily % change for relative context.
    Resilient: a failed peer leg yields null price/change rather than raising;
    an unmapped ticker returns an empty peer list with a note.
    """
    from app.watchlist import competitors_for, normalize

    t = normalize(ticker)
    peers = list(competitors_for(t))[:limit]
    if not peers:
        return {"ticker": t, "peers": [], "note": "no competitor mapping for this instrument"}

    try:
        import yfinance as yf
    except ImportError:
        return {"ticker": t, "peers": [{"ticker": p} for p in peers], "error": "yfinance not installed"}

    out: list[dict] = []
    for p in peers:
        try:
            fi = yf.Ticker(p).fast_info
            last = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
            chg = round((float(last) - float(prev)) / float(prev) * 100, 2) if (last and prev) else None
            out.append({"ticker": p, "price": round(float(last), 2) if last else None, "change_pct": chg})
        except Exception as exc:
            log.warning("competitor leg %s failed: %s", p, exc)
            out.append({"ticker": p, "price": None, "change_pct": None})

    return {
        "ticker": t,
        "peers": out,
        "note": "Competitor read-through — peer prices/moves for relative-strength context, NOT trade signals.",
    }


def fetch_technical_indicators(ticker: str) -> dict[str, Any]:
    """Live RSI(14) / MACD(12,26,9) / Bollinger(20,2σ) from 5-minute bars.

    Uses the SAME math as the ingestion cache (app.ta_indicators), so the live
    /analyze path and the offline continuous loop agree. Resilient: returns
    {"error": ...} on failure, never raises.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="5m", prepost=False)
        closes = hist["Close"].dropna().tolist() if hist is not None else []
        if len(closes) < 15:
            return {"error": f"insufficient intraday history for {ticker}"}
    except Exception as exc:
        log.warning("fetch_technical_indicators(%s) failed: %s", ticker, exc)
        return {"error": f"technical indicators unavailable for {ticker}: {exc}"}

    from app.ta_indicators import compute_indicators
    return {"ticker": ticker.upper(), **compute_indicators(closes)}


# ── crewai tool factory ─────────────────────────────────────────────────────

def build_finance_tools() -> list:
    """Wrap the fetch_* functions as crewai tools. Returns [] if crewai absent."""
    try:
        from crewai.tools import tool
    except ImportError:
        log.warning("crewai.tools unavailable — finance tools disabled")
        return []

    import json

    @tool("get_market_quote")
    def get_market_quote(ticker: str) -> str:
        """Live price and fundamentals (EPS, P/E, market cap) for a stock ticker."""
        return json.dumps(fetch_quote(ticker))

    # NOTE: every tool MUST declare at least one parameter. Groq's function-
    # calling validator rejects a zero-arg tool ("'required' present but
    # 'properties' is missing"), so the market-wide tools take an optional,
    # otherwise-ignored argument purely to produce a valid 'properties' schema.

    @tool("get_vix_curve")
    def get_vix_curve(symbol: str = "^VIX") -> str:
        """VIX term structure (9D/30D/3M), contango vs backwardation, and fear
        regime. The curve is market-wide; `symbol` is accepted for interface
        consistency and may be left as the default."""
        return json.dumps(fetch_vix_curve())

    @tool("get_options_sentiment")
    def get_options_sentiment(ticker: str) -> str:
        """Put/call ratio and ATM implied-vol skew from the public option chain."""
        return json.dumps(fetch_options_sentiment(ticker))

    @tool("get_spacex_launch_schedule")
    def get_spacex_launch_schedule(limit: str = "5") -> str:
        """Upcoming SpaceX launch cadence (Starship/Falcon) from a public API.
        `limit` caps how many upcoming launches to return (string-typed because
        some providers emit numeric tool args as strings)."""
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 5
        return json.dumps(fetch_launch_schedule(limit=n))

    @tool("get_macro_snapshot")
    def get_macro_snapshot(scope: str = "broad") -> str:
        """Broad-market macro backdrop: S&P 500 and NASDAQ level, daily % change,
        and overall risk-on/risk-off tone. Market-wide; `scope` is accepted for
        interface consistency and may be left as the default."""
        return json.dumps(fetch_macro_snapshot())

    @tool("get_competitor_analysis")
    def get_competitor_analysis(ticker: str) -> str:
        """Competitor/peer read-through for a ticker: each peer's live price and
        daily % change for relative-strength context (e.g. NVDA → AMD/AVGO/INTC/TSM)."""
        return json.dumps(fetch_competitors(ticker))

    @tool("get_technical_indicators")
    def get_technical_indicators(ticker: str) -> str:
        """Live technical indicators for a ticker — RSI(14), MACD(12/26/9) with
        cross direction, Bollinger(20,2σ) band position, and latest price."""
        return json.dumps(fetch_technical_indicators(ticker))

    return [
        get_market_quote, get_vix_curve, get_options_sentiment,
        get_spacex_launch_schedule, get_macro_snapshot, get_competitor_analysis,
        get_technical_indicators,
    ]
