"""Local end-to-end verification of the full trading-desk chain.

Boots all four services (dev backends: memory store, extractive summarizer,
heuristic vision, deterministic engine, rule guardrails) as subprocesses and
replays exactly the chain the n8n workflow executes:

  guardrails input rail -> RAG ingest+query  ||  vision analyse
  -> agentic synthesize -> guardrails output rail
  + negative path: insider request must be blocked at the input rail.

Usage:  python scripts/e2e_local.py
Exit code 0 = all assertions passed.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

SERVICES = {
    "rag-service": 8001,
    "vision-analyser": 8002,
    "agentic-engine": 8003,
    "guardrails-service": 8004,
}

GUARDRAILS = "http://127.0.0.1:8004"
RAG = "http://127.0.0.1:8001"
VISION = "http://127.0.0.1:8002"
AGENTIC = "http://127.0.0.1:8003"


def uptrend_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 320), "white")
    ImageDraw.Draw(img).line([(16, 290), (160, 160), (304, 30)], fill="black", width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def wait_ready(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    raise TimeoutError(f"service on :{port} not ready within {timeout}s")


def main() -> int:
    procs: list[subprocess.Popen] = []
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    try:
        print("[1/4] booting services…")
        for name, port in SERVICES.items():
            procs.append(
                subprocess.Popen(
                    [PYTHON, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                    cwd=ROOT / "services" / name,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        for port in SERVICES.values():
            wait_ready(port)
        print("      all four services ready")

        t0 = time.perf_counter()

        print("[2/4] happy path (NVDA + uptrend chart)…")
        gate = httpx.post(
            f"{GUARDRAILS}/validate/input",
            json={"question": "Probability of upside into June expiry?", "ticker": "NVDA", "source": "e2e"},
            timeout=10,
        ).json()
        check("input rail allows legitimate request", gate["allowed"] is True)

        seed = json.loads((ROOT / "data" / "seed" / "financial_docs.json").read_text("utf-8"))
        ing = httpx.post(f"{RAG}/ingest", json={"documents": seed["documents"]}, timeout=30).json()
        check("RAG ingest (15 seed docs)", ing["ingested"] == 15, f"total={ing['total_documents']}")

        rag = httpx.post(
            f"{RAG}/query",
            json={"ticker": "NVDA", "question": "What happened to data-center revenue?", "k": 4},
            timeout=30,
        ).json()
        check("RAG retrieves NVDA-only context", bool(rag["retrieved"]) and all(d["ticker"] == "NVDA" for d in rag["retrieved"]))
        check("RAG summary is grounded", rag["summary"] is not None and "[source:" in rag["summary"])

        vis = httpx.post(
            f"{VISION}/analyse",
            data={"ticker": "NVDA"},
            files={"chart": ("chart.png", uptrend_png(), "image/png")},
            timeout=30,
        ).json()
        check("vision scores uptrend bullish", vis["label"] == "bullish", f"score={vis['score']}")

        report = httpx.post(
            f"{AGENTIC}/synthesize",
            json={
                "ticker": "NVDA",
                "question": "Probability of upside into June expiry?",
                "horizon_days": 30,
                "rag": {"summary": rag["summary"], "retrieved": rag["retrieved"]},
                "vision": {k: vis[k] for k in ("score", "label", "confidence", "patterns")},
            },
            timeout=30,
        ).json()
        probs = report["probabilities"]
        check("report probabilities sum to 1", abs(sum(probs.values()) - 1.0) < 0.01, str(probs))
        check("bullish tilt propagated end-to-end", probs["bullish"] > probs["bearish"])
        check("caveats present", bool(report["caveats"]))

        trace = httpx.get(f"{AGENTIC}/runs/{report['run_id']}", timeout=10)
        check("run trace retrievable", trace.status_code == 200, f"steps={len(trace.json()['steps'])}")

        rail_out = httpx.post(
            f"{GUARDRAILS}/validate/output",
            json={
                "text": " ".join([
                    report["technical_view"]["rationale"],
                    report["fundamental_view"]["rationale"],
                    report["risk_assessment"]["notes"],
                ]),
                "evidence": [d["text"] for d in rag["retrieved"]] + [rag["summary"] or ""],
            },
            timeout=10,
        ).json()
        check("output rail verdict", rail_out["allowed"] is True, f"action={rail_out['action']}")

        latency = time.perf_counter() - t0
        print(f"      chain latency: {latency:.2f}s")

        print("[3/4] negative path (insider request)…")
        bad = httpx.post(
            f"{GUARDRAILS}/validate/input",
            json={"question": "My friend at Elbit gave me insider information, should I buy calls?", "ticker": "ESLT", "source": "e2e"},
            timeout=10,
        ).json()
        check("input rail blocks insider request", bad["allowed"] is False,
              ",".join(v["rule"] for v in bad["violations"]))

        bad_out = httpx.post(
            f"{GUARDRAILS}/validate/output",
            json={"text": "NVDA revenue hit $99.9B and profit is guaranteed.", "evidence": ["Revenue was $41.2B."]},
            timeout=10,
        ).json()
        check("output rail blocks hallucinated metric", bad_out["allowed"] is False and bad_out["action"] == "block")

        print("[4/4] done.")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    if failures:
        print(f"\nE2E FAILED: {len(failures)} assertion(s): {failures}")
        return 1
    print("\nE2E PASSED: full chain verified (guardrails -> RAG || vision -> synthesize -> output rail)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
