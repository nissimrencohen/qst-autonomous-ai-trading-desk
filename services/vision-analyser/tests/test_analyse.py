"""Vision Analyser endpoint tests (heuristic backend — no torch needed)."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app

client = TestClient(app)


def _chart_png(points: list[tuple[float, float]], size: int = 320) -> bytes:
    """Render a synthetic line chart; points are (x, y) in [0,1], y up."""
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    pixels = [(x * (size - 1), (1 - y) * (size - 1)) for x, y in points]
    draw.line(pixels, fill="black", width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _analyse(png: bytes, ticker: str = "NVDA"):
    return client.post(
        "/analyse",
        data={"ticker": ticker},
        files={"chart": ("chart.png", png, "image/png")},
    )


def test_uptrend_chart_scores_bullish() -> None:
    res = _analyse(_chart_png([(0.05, 0.1), (0.5, 0.5), (0.95, 0.9)]))
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "NVDA"
    assert body["score"] > 0.15
    assert body["label"] == "bullish"
    assert set(body["patterns"]) == {
        "support_bounce", "resistance_rejection", "breakout_up",
        "breakdown", "consolidation",
    }
    assert all(0.0 <= v <= 1.0 for v in body["patterns"].values())


def test_downtrend_chart_scores_bearish() -> None:
    res = _analyse(_chart_png([(0.05, 0.9), (0.5, 0.5), (0.95, 0.1)]), ticker="eslt")
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "ESLT"  # normalized to upper case
    assert body["score"] < -0.15
    assert body["label"] == "bearish"


def test_flat_chart_scores_neutral() -> None:
    res = _analyse(_chart_png([(0.05, 0.5), (0.95, 0.5)]), ticker="TOND")
    body = res.json()
    assert res.status_code == 200
    assert body["label"] == "neutral"
    assert body["patterns"]["consolidation"] > 0.5


def test_rejects_non_image_content_type() -> None:
    res = client.post(
        "/analyse",
        data={"ticker": "CUE"},
        files={"chart": ("notes.txt", b"not an image", "text/plain")},
    )
    assert res.status_code == 415


def test_rejects_corrupt_image_bytes() -> None:
    res = _analyse(b"\x89PNG but actually garbage")
    assert res.status_code == 422


def test_ready_reports_analyser_loaded() -> None:
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["checks"]["analyser"] is True
