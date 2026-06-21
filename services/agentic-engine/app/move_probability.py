"""Quantitative probability-of-move calculator.

Computes P(|return| > r%) using the GBM lognormal model, the same
closed-form framework as forecast.py — so probabilities are internally
consistent with the forecast chart bands.

No scipy needed: uses math.erfc for the standard-normal CDF.

Key functions
─────────────
calc_move_probs()     : P(>1%), P(>2%), P(>3%), P(>5%), P(>10%) for any horizon
vix_implied_probs()   : infer daily move probs directly from VIX level
instrument_probs()    : full suite for one instrument, pulling live vol from yfinance
"""
from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger(__name__)

# Standard thresholds (%).
_THRESHOLDS = [1, 2, 3, 5, 10]

# Approximate UVXY/SVXY vol multiplier vs VIX futures.
_UVXY_LEVERAGE = 1.5   # UVXY is 1.5× VIX short-term futures
_SVXY_LEVERAGE = -0.5  # SVXY is -0.5× (inverse)


# ── numerics ──────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF — accurate to 7 sig figs via math.erfc."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _p_above(log_return_threshold: float, mu_adj: float, sigma_t: float) -> float:
    """P(log_return > log_return_threshold) under lognormal GBM."""
    if sigma_t <= 0:
        return 0.0
    d = (log_return_threshold - mu_adj) / sigma_t
    return max(0.0, min(1.0, 1.0 - _norm_cdf(d)))


def _p_below(log_return_threshold: float, mu_adj: float, sigma_t: float) -> float:
    """P(log_return < log_return_threshold) under lognormal GBM."""
    if sigma_t <= 0:
        return 0.0
    d = (log_return_threshold - mu_adj) / sigma_t
    return max(0.0, min(1.0, _norm_cdf(d)))


# ── core calculator ───────────────────────────────────────────────────────────

def calc_move_probs(
    vol_annual: float,
    drift_annual: float = 0.0,
    horizon_trading_days: int = 1,
    thresholds: list[int] = _THRESHOLDS,
) -> dict[str, float]:
    """Return P(up>r%), P(down>r%), P(|move|>r%) for each threshold.

    Args:
        vol_annual: annualised vol (e.g. 0.30 = 30%)
        drift_annual: annualised log-drift, tilted by crew probabilities
        horizon_trading_days: 1 = today, 5 = this week, etc.
        thresholds: list of % thresholds (integers)

    Returns dict with keys like "p_up_2pct", "p_down_2pct", "p_move_2pct".
    Also includes "expected_daily_range_pct" and "vol_daily_pct".
    """
    t = max(horizon_trading_days, 1) / 252.0
    # Ito drift adjustment: log-return drift = μ - σ²/2
    mu_adj = (drift_annual - 0.5 * vol_annual ** 2) * t
    sigma_t = vol_annual * math.sqrt(t)

    result: dict[str, float] = {}
    for r in thresholds:
        lr_up   = math.log(1 + r / 100)
        lr_down = math.log(1 - r / 100)
        p_up   = _p_above(lr_up,   mu_adj, sigma_t)
        p_down = _p_below(lr_down, mu_adj, sigma_t)
        result[f"p_up_{r}pct"]   = round(p_up,  4)
        result[f"p_down_{r}pct"] = round(p_down, 4)
        result[f"p_move_{r}pct"] = round(min(1.0, p_up + p_down), 4)

    # Expected 1-day ±1σ range (informational)
    daily_vol = vol_annual / math.sqrt(252)
    result["vol_daily_pct"]          = round(daily_vol * 100, 2)
    result["expected_daily_range_pct"] = round(daily_vol * 100 * 2, 2)  # ±1σ = 2σ range
    return result


# ── VIX-implied probabilities ─────────────────────────────────────────────────

def vix_implied_probs(
    vix_level: float,
    ticker: str = "SPY",
    horizon_trading_days: int = 1,
) -> dict[str, Any]:
    """Derive move probabilities from VIX level alone.

    VIX = annualised implied vol (in %). Adjust for instrument type:
    - UVXY: 1.5× leverage on VIX front-month futures
    - SVXY: 0.5× inverse (negative drift from contango + 0.5× exposure)
    - General equity: use VIX directly as a proxy for implied vol

    These are market-implied probabilities (risk-neutral), not physical.
    """
    ticker_u = ticker.upper().lstrip("$").strip()
    vol_ann = vix_level / 100.0  # VIX is in %, convert to decimal

    if ticker_u in ("UVXY", "VXX"):
        vol_ann = vol_ann * _UVXY_LEVERAGE * 2.2  # empirical: UVXY realized vol ≈ 1.5x VIX × 2
        drift = -0.40  # structural contango decay ≈ -40% annualised when VIX < 20
    elif ticker_u in ("SVXY",):
        vol_ann = vol_ann * abs(_SVXY_LEVERAGE) * 2.0
        drift = +0.25  # inverse: gains from contango decay
    else:
        drift = 0.0  # risk-neutral: no drift assumption

    probs = calc_move_probs(
        vol_annual=vol_ann,
        drift_annual=drift,
        horizon_trading_days=horizon_trading_days,
    )
    probs["_source"] = "vix_implied"
    probs["_vix_input"] = round(vix_level, 2)
    probs["_ticker"] = ticker_u
    return probs


# ── instrument-level (uses live yfinance vol) ─────────────────────────────────

def _fetch_vol_and_drift(ticker: str, crew_bull: float = 0.0, crew_bear: float = 0.0) -> tuple[float, float]:
    """Get annualised vol + crew-tilted drift for a ticker via yfinance.

    Returns (vol_annual, drift_annual). Falls back to VIX=20 proxy on error.
    """
    try:
        import yfinance as yf
        import pandas as pd

        hist = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
        if len(hist) < 10:
            raise ValueError("insufficient history")
        log_rets = (hist["Close"] / hist["Close"].shift(1)).dropna().apply(math.log)
        vol_daily = float(log_rets.std())
        vol_annual = vol_daily * math.sqrt(252)

        # Annualised historical drift
        n = len(log_rets)
        total_log_ret = float(log_rets.sum())
        drift_raw = total_log_ret / (n / 252.0)

        # Crew tilt: blend historical drift with crew directional signal
        directional_bias = crew_bull - crew_bear          # in [-1, 1]
        drift_tilt = directional_bias * 0.20 * vol_annual  # max ±20% of vol
        drift_annual = drift_raw + drift_tilt

        return vol_annual, drift_annual
    except Exception as exc:
        log.warning("vol fetch failed for %s: %s — using VIX=20 proxy", ticker, exc)
        return 0.20, 0.0   # default: 20% annual vol, no drift assumption


def instrument_probs(
    ticker: str,
    crew_bullish: float = 0.0,
    crew_bearish: float = 0.0,
    vix_level: float | None = None,
    horizon_trading_days: int = 1,
) -> dict[str, Any]:
    """Full probability suite for one instrument.

    Combines historical vol (yfinance) + crew directional tilt.
    For UVXY/SVXY, also includes VIX-implied comparison.

    Returns dict with:
      vol_annual, drift_annual, thresholds (p_up_Xpct, p_down_Xpct, p_move_Xpct),
      vol_daily_pct, expected_daily_range_pct
    """
    ticker_u = ticker.upper().lstrip("$").strip()
    vol_annual, drift_annual = _fetch_vol_and_drift(ticker_u, crew_bullish, crew_bearish)

    result = calc_move_probs(
        vol_annual=vol_annual,
        drift_annual=drift_annual,
        horizon_trading_days=horizon_trading_days,
    )
    result["_source"]       = "historical_vol"
    result["_ticker"]       = ticker_u
    result["vol_annual"]    = round(vol_annual, 4)
    result["drift_annual"]  = round(drift_annual, 4)

    # For VIX products add VIX-implied overlay
    if ticker_u in ("UVXY", "SVXY", "VXX") and vix_level:
        vix_overlay = vix_implied_probs(vix_level, ticker_u, horizon_trading_days)
        # Take the average of historical and VIX-implied (both carry information)
        for key in list(result.keys()):
            if key.startswith("p_"):
                vk = vix_overlay.get(key)
                if vk is not None:
                    result[key] = round((result[key] + vk) / 2, 4)
        result["_vix_overlay_applied"] = True

    return result
