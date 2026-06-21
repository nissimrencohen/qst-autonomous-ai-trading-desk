"""Pure-Python technical indicators — single source of truth.

Both the ingestion engine (writes ta_signal rows) and the live finance tools
(get_technical_indicators) compute indicators from here, so the offline cache and
the live path agree exactly. No third-party TA dependency.
"""
from __future__ import annotations

import math
from typing import Any


def compute_rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index. Requires len(closes) >= period + 1."""
    if len(closes) < period + 1:
        return 50.0  # neutral fallback
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(data: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if not data:
        return []
    k = 2.0 / (period + 1)
    result = [data[0]]
    for i in range(1, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result


def compute_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[float, float, float]:
    """MACD line, Signal line, Histogram (latest values)."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], histogram


def compute_bollinger(
    closes: list[float], period: int = 20, num_std: int = 2,
) -> tuple[float, float, float]:
    """Bollinger Band upper, mid, lower (latest values)."""
    window = closes[-period:] if len(closes) >= period else closes
    mid = sum(window) / len(window)
    variance = sum((x - mid) ** 2 for x in window) / len(window)
    std = math.sqrt(variance)
    return mid + num_std * std, mid, mid - num_std * std


def compute_indicators(closes: list[float]) -> dict[str, Any]:
    """Full indicator bundle (RSI / MACD / Bollinger) from a close series.

    Returns the dict stored in ta_signal.meta_json and returned by the
    get_technical_indicators tool. Each block degrades independently.
    """
    ind: dict[str, Any] = {}

    # ── RSI(14) ──
    try:
        rsi = compute_rsi(closes, period=14)
        ind["rsi"] = round(rsi, 2)
        ind["rsi_signal"] = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    except Exception:
        ind["rsi"] = None
        ind["rsi_signal"] = "error"

    # ── MACD(12,26,9) ──
    try:
        macd_line, signal_line, histogram = compute_macd(closes)
        ind["macd"] = round(macd_line, 4)
        ind["macd_signal"] = round(signal_line, 4)
        ind["macd_histogram"] = round(histogram, 4)
        if histogram > 0 and macd_line > signal_line:
            ind["macd_cross"] = "bullish"
        elif histogram < 0 and macd_line < signal_line:
            ind["macd_cross"] = "bearish"
        else:
            ind["macd_cross"] = "neutral"
    except Exception:
        ind["macd_cross"] = "error"

    # ── Bollinger Bands (20, 2σ) ──
    try:
        bb_upper, bb_mid, bb_lower = compute_bollinger(closes, period=20, num_std=2)
        price = closes[-1]
        ind["bb_upper"] = round(bb_upper, 2)
        ind["bb_mid"] = round(bb_mid, 2)
        ind["bb_lower"] = round(bb_lower, 2)
        ind["price"] = round(price, 2)
        if price > bb_upper:
            ind["bb_position"] = "above_upper"
        elif price < bb_lower:
            ind["bb_position"] = "below_lower"
        elif price > bb_mid:
            ind["bb_position"] = "upper_half"
        else:
            ind["bb_position"] = "lower_half"
    except Exception:
        ind["bb_position"] = "error"

    return ind
