"""Mandatory Macro & Fear context (mission Req 2).

Every single-ticker analysis MUST be assessed against the broad market
(S&P 500 / NASDAQ) and the fear index (VIX). This module composes those two
data sources into one text block that the orchestrator injects into the
existing `{macro_context}` prompt placeholder for EVERY `/analyze` request —
not just the daily briefing.

Rate-limit safety: the underlying market data (which is market-wide and
identical for all tickers in a burst) is cached for `_TTL` seconds, so a batch
of ten tickers triggers at most one macro+VIX fetch. The fetchers themselves
degrade to `{"error": ...}` on any provider failure, so `build_desk_context`
ALWAYS returns a non-empty block — never raising, never blocking analysis.
"""
from __future__ import annotations

import logging
import time

from app.watchlist import normalize

log = logging.getLogger(__name__)

_TTL = 60.0  # seconds — one macro/VIX fetch per minute is plenty for the desk
_cache: dict = {"data": None, "ts": 0.0}


def _macro_vix_data(*, force: bool = False) -> dict:
    """Cached market-wide macro snapshot + VIX curve (shared across tickers)."""
    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    # Imported lazily so importing this module never pulls in yfinance.
    from app.finance_tools import fetch_macro_snapshot, fetch_vix_curve

    data = {"macro": fetch_macro_snapshot(), "vix": fetch_vix_curve()}
    _cache["data"] = data
    _cache["ts"] = now
    return data


def reset_cache() -> None:
    """Clear the macro/VIX cache (used by tests; safe to call anytime)."""
    _cache["data"] = None
    _cache["ts"] = 0.0


def _format_desk_context(t: str, macro: dict, vix: dict, *, freshness: str = "") -> str:
    """Shared formatter for the mandatory macro+fear block (live or store-backed)."""
    lines = [
        f"MANDATORY MACRO & FEAR CONTEXT for {t} (applies to every desk analysis):",
    ]

    # ── Broad market (S&P 500 / NASDAQ) ──────────────────────────────────────
    if "error" not in macro:
        sp, ndx = macro.get("sp500"), macro.get("nasdaq")
        sp_s = f"S&P 500 {sp['price']} ({sp['change_pct']:+.2f}%)" if sp else "S&P 500 n/a"
        ndx_s = f"NASDAQ {ndx['price']} ({ndx['change_pct']:+.2f}%)" if ndx else "NASDAQ n/a"
        lines.append(f"  Broad market: {sp_s} | {ndx_s} -> {macro.get('market_tone', 'n/a')}")
    else:
        lines.append(f"  Broad market: unavailable ({macro['error']})")

    # ── Fear index (VIX term structure / regime) ─────────────────────────────
    if "error" not in vix:
        lines.append(
            f"  Fear index (VIX): {vix.get('vix_30d')} | "
            f"{vix.get('term_structure')} | regime={vix.get('regime')}"
        )
    else:
        lines.append(f"  Fear index (VIX): unavailable ({vix['error']})")

    if freshness:
        lines.append(f"  {freshness}")

    lines.append(
        f"  Assess explicitly how this broad-market backdrop and fear regime "
        f"affect {t} (its beta to the index and sensitivity to a volatility spike)."
    )
    return "\n".join(lines)


def build_desk_context(ticker: str, *, force_refresh: bool = False) -> str:
    """Compose the mandatory macro + fear backdrop for `ticker` from LIVE data.

    Used by the on-demand /analyze path. Always returns a non-empty string;
    degrades gracefully ("unavailable") if either data source errors out.
    """
    t = normalize(ticker)
    data = _macro_vix_data(force=force_refresh)
    return _format_desk_context(t, data["macro"], data["vix"])


def build_desk_context_from_store(store, ticker: str, *, stale_minutes: int = 15) -> str:
    """Compose the mandatory macro + fear backdrop from the IngestionStore cache.

    Used by the Continuous Synthesis Loop (Step 2e) — NO live calls. Reads the
    market-wide 'MACRO' and 'VIX' rows the ingestion engine wrote, and flags the
    data as stale if it is older than `stale_minutes`. Always non-empty.
    """
    from datetime import datetime, timezone

    t = normalize(ticker)
    macro_rows = store.query_latest("MACRO", "macro", 1)
    vix_rows = store.query_latest("VIX", "macro", 1)
    macro = macro_rows[0].meta if macro_rows else {"error": "no cached macro snapshot"}
    vix = vix_rows[0].meta if vix_rows else {"error": "no cached VIX data"}

    # Freshness: derive age from the newest of the two rows' ingested_at.
    freshness = ""
    stamps = [r.ingested_at for r in (*macro_rows, *vix_rows) if r.ingested_at]
    if stamps:
        newest = max(stamps)
        try:
            age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds() / 60
            if age_min > stale_minutes:
                freshness = f"(NOTE: cached market data is stale — {age_min:.0f} min old, as of {newest})"
            else:
                freshness = f"(as of {newest}, {age_min:.0f} min old)"
        except (ValueError, TypeError):
            freshness = f"(as of {newest})"

    return _format_desk_context(t, macro, vix, freshness=freshness)
