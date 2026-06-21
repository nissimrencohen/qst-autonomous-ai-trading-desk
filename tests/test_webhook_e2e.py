"""E2E smoke test — n8n webhook → agentic /analyze orchestration.

Posts an analysis request (with a freshly-generated chart) to the n8n webhook
and prints the resulting run/report. Self-contained: the chart is rendered live
from yfinance via scripts/make_chart.py — no checked-in image asset required.

Usage:
    python tests/test_webhook_e2e.py [TICKER]      # default: NVDA
Requires the stack + the n8n analyze workflow to be running.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# make scripts/make_chart.py importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_chart import chart_base64  # noqa: E402

WATCHLIST = {"SPCX", "MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "UPRO", "TQQQ", "VIXY", "SVXY"}


def run_e2e_test(ticker: str = "NVDA") -> None:
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        print(f"⚠️  {ticker} is not on the V2.0 watchlist {sorted(WATCHLIST)} — it will be rejected.")

    print(f"1. Generating a live chart for {ticker}...")
    chart_b64, content_type = chart_base64(ticker)
    print("   chart:", "ok" if chart_b64 else "unavailable (continuing without chart)")

    payload = {
        "ticker": ticker,
        "question": f"Provide a comprehensive technical and fundamental analysis for {ticker}.",
        "horizon_days": 30,
    }
    if chart_b64:
        payload["chart_base64"] = chart_b64
        payload["chart_content_type"] = content_type

    data = json.dumps(payload).encode("utf-8")
    endpoints = [
        "http://localhost:5678/webhook/analyze",
        "http://localhost:5678/webhook-test/analyze",
    ]

    print(f"2. Sending request to n8n orchestrator... (Payload size: {len(data) / 1024:.1f} KB)")

    success = False
    for url in endpoints:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            t0 = time.perf_counter()
            with urllib.request.urlopen(req) as response:
                latency = time.perf_counter() - t0
                raw_resp = response.read().decode("utf-8")
                print(f"\n✅ SUCCESS! (URL: {url})")
                print(f"⏱️  Latency: {latency:.2f}s")
                print("\n--- Raw Response ---")
                print(raw_resp)
                try:
                    result = json.loads(raw_resp)
                    print("\n--- Response (parsed) ---")
                    print(json.dumps(result, indent=2))
                except json.JSONDecodeError:
                    print("\n⚠️ Response is not JSON!")
                success = True
                break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # try the next endpoint
            print(f"❌ HTTP Error {e.code} from {url}: {e.read().decode('utf-8', errors='replace')}")
            return
        except urllib.error.URLError as e:
            print(f"❌ Network Error connecting to {url}: {e.reason}")
            return

    if not success:
        print("❌ Could not reach the n8n webhook. Is the workflow activated?")


if __name__ == "__main__":
    run_e2e_test(sys.argv[1] if len(sys.argv) > 1 else "NVDA")
