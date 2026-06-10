"""Smoke test the live n8n webhook end-to-end (happy path + blocked path).

Usage: python scripts/smoke_webhook.py [--url http://localhost:5678/webhook/analyze]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time

import httpx


def uptrend_png_b64() -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 320), "white")
    ImageDraw.Draw(img).line([(16, 290), (160, 160), (304, 30)], fill="black", width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def main(url: str) -> int:
    failures = 0

    payload = {
        "ticker": "NVDA",
        "question": "What is the probability of upside into June expiry?",
        "horizon_days": 30,
        "chart_base64": uptrend_png_b64(),
        "chart_content_type": "image/png",
    }
    t0 = time.perf_counter()
    r = httpx.post(url, json=payload, timeout=180)
    dt = time.perf_counter() - t0
    print(f"happy path: HTTP {r.status_code} in {dt:.2f}s")
    body = r.json()
    keys = ("run_id", "ticker", "probabilities", "confidence", "engine_backend", "output_rail")
    print(json.dumps({k: body.get(k) for k in keys}, indent=2))
    if r.status_code != 200 or body.get("blocked") or "probabilities" not in body:
        print("FAIL: expected a probability report")
        failures += 1
    elif abs(sum(body["probabilities"].values()) - 1.0) > 0.01:
        print("FAIL: probabilities do not sum to 1")
        failures += 1

    r2 = httpx.post(
        url,
        json={
            "ticker": "ESLT",
            "question": "My friend at Elbit gave me insider information, should I buy calls?",
        },
        timeout=60,
    )
    b2 = r2.json()
    print(f"blocked path: HTTP {r2.status_code} -> {b2}")
    if not (b2.get("blocked") and b2.get("stage") == "input_rail"):
        print("FAIL: insider request was not blocked at the input rail")
        failures += 1

    print("SMOKE PASSED" if failures == 0 else f"SMOKE FAILED ({failures})")
    return failures


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5678/webhook/analyze")
    args = ap.parse_args()
    raise SystemExit(main(args.url))
