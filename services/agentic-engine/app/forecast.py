"""Predictive price forecast for the dashboard chart (Phase 3).

A transparent Geometric Brownian Motion projection: estimate per-bar drift μ and
volatility σ from recent log-returns, **tilt the drift by the crew's directional
bias** (bullish − bearish) so the chart reflects the desk's thesis (the
"intertwining"), then emit closed-form lognormal p10/p50/p90 quantile bands whose
width grows with the horizon.

Honesty: this is a baseline stochastic model, NOT a precision predictor — no
regime-switching, Gaussian (thin) tails, constant vol. It visualises a plausible
cone of outcomes consistent with the thesis, not a forecast to trade blindly.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from app.schemas import Forecast, ForecastPoint

log = logging.getLogger(__name__)

# standard-normal quantiles for the 10th / 50th / 90th percentiles
_Z10, _Z90 = -1.2815515594, 1.2815515594
_TILT = 0.5  # crew bias shifts per-bar drift by up to ±0.5σ

# trading bars per year, per interval — for annualised display only
_ANNUALIZE = {"1d": 252.0, "1h": 252.0 * 6.5, "30m": 252.0 * 13,
              "15m": 252.0 * 26, "5m": 252.0 * 78}
_INTERVAL_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}


def build_forecast(
    ticker: str,
    steps: int,
    bullish: float,
    bearish: float,
    interval: str = "1d",
    history_period: str = "6mo",
    history_points: int = 60,
) -> Forecast | None:
    """Return a GBM Forecast for `ticker` over `steps` future bars, or None if
    history is unavailable / insufficient. Never raises."""
    try:
        import numpy as np
        import pandas as pd
        import yfinance as yf
    except ImportError:
        return None

    steps = max(1, min(int(steps), 365))
    try:
        closes = yf.Ticker(ticker).history(period=history_period, interval=interval)["Close"].dropna()
    except Exception as exc:
        log.warning("forecast history fetch failed for %s: %s", ticker, exc)
        return None
    if len(closes) < 20:
        return None

    idx = list(closes.index)
    vals = closes.to_numpy(dtype=float)
    logret = np.diff(np.log(vals))
    mu = float(np.mean(logret))
    sigma = float(np.std(logret, ddof=1))
    if not math.isfinite(sigma) or sigma <= 0:
        return None

    bias = max(-1.0, min(1.0, float(bullish) - float(bearish)))
    mu_adj = mu + bias * _TILT * sigma
    s0 = float(vals[-1])
    ann = _ANNUALIZE.get(interval, 252.0)

    tail = min(history_points, len(vals))
    history = [
        ForecastPoint(t=idx[i].isoformat(), close=round(float(vals[i]), 4))
        for i in range(len(vals) - tail, len(vals))
    ]

    last = idx[-1]
    if interval == "1d":
        future = pd.bdate_range(start=last, periods=steps + 1)[1:]
    else:
        mins = _INTERVAL_MINUTES.get(interval, 5)
        future = pd.date_range(start=last, periods=steps + 1, freq=f"{mins}min")[1:]

    # anchor the projection on the last actual close so the lines connect
    projection = [ForecastPoint(t=last.isoformat(), p10=round(s0, 4), p50=round(s0, 4), p90=round(s0, 4))]
    for j, ts in enumerate(future, start=1):
        drift_t = (mu_adj - 0.5 * sigma**2) * j
        vol_t = sigma * math.sqrt(j)
        projection.append(ForecastPoint(
            t=ts.isoformat(),
            p10=round(s0 * math.exp(drift_t + _Z10 * vol_t), 4),
            p50=round(s0 * math.exp(drift_t), 4),
            p90=round(s0 * math.exp(drift_t + _Z90 * vol_t), 4),
        ))

    return Forecast(
        ticker=ticker.upper(),
        interval=interval,
        model="GBM closed-form bands · drift tilted by crew thesis",
        anchor_price=round(s0, 4),
        drift_annual=round(mu_adj * ann, 4),
        vol_annual=round(sigma * math.sqrt(ann), 4),
        directional_bias=round(bias, 3),
        history=history,
        projection=projection,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
