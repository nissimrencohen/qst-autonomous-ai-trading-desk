"""Live LLM-layer smoke: real Ollama summarization + free-text extraction path.

Usage: python scripts/smoke_llm.py
Requires: stack up (docker compose), n8n workflow published, Ollama on :11434.
"""
from __future__ import annotations

import json
import time

import httpx

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


print("[1/3] RAG /query with live Ollama summarizer (qwen3:8b)…")
t0 = time.perf_counter()
r = httpx.post(
    "http://localhost:8001/query",
    json={"ticker": "NVDA", "question": "What happened to data-center revenue and what is the options market pricing?", "k": 3},
    timeout=300,
)
dt = time.perf_counter() - t0
body = r.json()
print(f"      latency: {dt:.1f}s, backend: {body.get('summarizer_backend')}")
print("      summary:\n" + "\n".join("      | " + ln for ln in (body.get("summary") or "").splitlines()[:10]))
check("summarizer backend is ollama", body.get("summarizer_backend") == "ollama")
summary = body.get("summary") or ""
check("summary is non-empty and clean of <think>", bool(summary) and "<think>" not in summary)
check("summary grounded (quotes a seeded figure)", any(tok in summary for tok in ("41.2", "38%", "52%", "1300")))

print("[2/3] full n8n path with explicit ticker + chart…")
t0 = time.perf_counter()
import base64, io
from PIL import Image, ImageDraw
img = Image.new("RGB", (320, 320), "white")
ImageDraw.Draw(img).line([(16, 290), (160, 160), (304, 30)], fill="black", width=4)
buf = io.BytesIO(); img.save(buf, format="PNG")
r2 = httpx.post(
    "http://localhost:3002/webhook/analyze",
    json={
        "ticker": "NVDA",
        "question": "Probability of upside into June expiry?",
        "horizon_days": 30,
        "chart_base64": base64.b64encode(buf.getvalue()).decode(),
        "chart_content_type": "image/png",
    },
    timeout=300,
)
dt2 = time.perf_counter() - t0
rep = r2.json()
print(f"      latency: {dt2:.1f}s, probabilities: {rep.get('probabilities')}")
check("report produced through n8n with LLM summarizer", r2.status_code == 200 and "probabilities" in rep, f"{dt2:.1f}s")
check("output rail verdict present", rep.get("output_rail", {}).get("action") in ("pass", "sanitize"))

print("[3/3] free-text path (no ticker) -> Ollama extractor…")
t0 = time.perf_counter()
r3 = httpx.post(
    "http://localhost:3002/webhook/analyze",
    json={"question": "What are the odds of upside for Nvidia into the monthly expiry?"},
    timeout=300,
)
dt3 = time.perf_counter() - t0
rep3 = r3.json()
print(f"      latency: {dt3:.1f}s, ticker: {rep3.get('ticker')}, horizon: {rep3.get('horizon_days')}")
check("extractor resolved Nvidia -> NVDA", rep3.get("ticker") == "NVDA", f"{dt3:.1f}s")
check("horizon normalized (30d)", rep3.get("horizon_days") == 30)

print()
print("LLM SMOKE PASSED" if failures == 0 else f"LLM SMOKE FAILED ({failures})")
raise SystemExit(failures)
