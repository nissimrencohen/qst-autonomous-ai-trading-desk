"""Render a real candlestick PNG from yfinance OHLC (PIL only, no matplotlib).

Used to exercise the LLM vision backend end-to-end with a genuine chart image.
    python scripts/make_chart.py NVDA  ->  ./<TICKER>_chart.png

Also importable by the E2E tests:
    from make_chart import chart_base64
    b64, content_type = chart_base64("NVDA")   # (None, None) on any failure
"""
from __future__ import annotations

import base64
import io
import sys
from PIL import Image, ImageDraw, ImageFont

W, H = 900, 500
PAD_L, PAD_R, PAD_T, PAD_B = 60, 20, 40, 30
BG = (10, 14, 18)
GRID = (40, 52, 64)
UP = (45, 212, 167)
DOWN = (255, 93, 93)
TXT = (215, 224, 232)


def fetch_ohlc(ticker: str, days: int = 60):
    import yfinance as yf

    h = yf.Ticker(ticker).history(period=f"{days}d", interval="1d")
    rows = [
        (i, float(r.Open), float(r.High), float(r.Low), float(r.Close))
        for i, r in enumerate(h.itertuples())
    ]
    return rows


def build_image(ticker: str, rows) -> "Image.Image":
    """Build the candlestick chart image in memory (no file I/O)."""
    lows = [r[3] for r in rows]
    highs = [r[2] for r in rows]
    lo, hi = min(lows), max(highs)
    rng = hi - lo or 1.0
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    n = len(rows)
    cw = plot_w / n

    def y(v):
        return PAD_T + plot_h - (v - lo) / rng * plot_h

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        small = ImageFont.truetype("arial.ttf", 11)
    except Exception:
        font = small = ImageFont.load_default()

    # gridlines + price labels
    for k in range(5):
        gy = PAD_T + plot_h * k / 4
        d.line([(PAD_L, gy), (W - PAD_R, gy)], fill=GRID)
        price = hi - rng * k / 4
        d.text((6, gy - 6), f"{price:.0f}", fill=TXT, font=small)

    # candles
    for idx, o, hgh, lo_, c in rows:
        cx = PAD_L + cw * (idx + 0.5)
        col = UP if c >= o else DOWN
        d.line([(cx, y(hgh)), (cx, y(lo_))], fill=col)  # wick
        bw = max(2, cw * 0.6)
        top, bot = y(max(o, c)), y(min(o, c))
        d.rectangle([cx - bw / 2, top, cx + bw / 2, max(bot, top + 1)], fill=col)

    d.text((PAD_L, 10), f"{ticker}  daily  ({rows[0][4]:.2f} -> {rows[-1][4]:.2f})",
           fill=TXT, font=font)
    return img


def render(ticker: str, rows) -> str:
    """Build the chart and save it to ./<TICKER>_chart.png; returns the path."""
    out = f"{ticker}_chart.png"
    build_image(ticker, rows).save(out)
    return out


def render_bytes(ticker: str, days: int = 60) -> bytes:
    """Fetch OHLC, render, and return PNG bytes (no file I/O)."""
    buf = io.BytesIO()
    build_image(ticker.upper(), fetch_ohlc(ticker, days)).save(buf, format="PNG")
    return buf.getvalue()


def chart_base64(ticker: str) -> tuple[str | None, str | None]:
    """Return (base64-PNG, "image/png") for a live chart, or (None, None) on any
    failure (e.g. no network / yfinance unavailable). Lets E2E tests stay
    self-contained — no checked-in chart asset required."""
    try:
        return base64.b64encode(render_bytes(ticker)).decode("utf-8"), "image/png"
    except Exception as exc:  # noqa: BLE001
        print(f"[make_chart] chart generation failed for {ticker}: {exc}")
        return None, None


if __name__ == "__main__":
    tk = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
    path = render(tk, fetch_ohlc(tk))
    print(path)
