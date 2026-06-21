import base64
import io
import json
import sys
import time
import requests
from pathlib import Path

# make scripts/make_chart.py importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_chart import chart_base64  # noqa: E402

WATCHLIST = {"SPCX", "MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "UPRO", "TQQQ", "VIXY", "SVXY"}


def run_direct_e2e(ticker: str = "NVDA"):
    ticker = ticker.upper()
    question = f"Provide a comprehensive technical and fundamental analysis for {ticker}."

    t0 = time.perf_counter()

    try:
        # 1. Guardrails Input
        print("1. Guardrails Input (/validate/input)...")
        in_res = requests.post("http://localhost:8004/validate/input", json={"ticker": ticker, "question": question, "source": "e2e_test"})
        in_res.raise_for_status()
        in_rail = in_res.json()
        if not in_rail.get("allowed"):
            print(f"Blocked by input rail: {in_rail}")
            return

        # 2. Vision Analyser — render a live chart (no checked-in asset). Optional:
        #    if generation/analysis fails, the pipeline proceeds without vision.
        print("2. Vision Analyser (/analyse)...")
        vision_res = None
        chart_b64, _ = chart_base64(ticker)
        if chart_b64:
            img_bytes = base64.b64decode(chart_b64)
            vision_res_raw = requests.post(
                "http://localhost:8002/analyse",
                data={"ticker": ticker},
                files={"chart": (f"{ticker}_chart.png", io.BytesIO(img_bytes), "image/png")},
            )
            vision_res_raw.raise_for_status()
            vision_res = vision_res_raw.json()
            print(f"   vision: {vision_res.get('label')} ({vision_res.get('model_backend')})")
        else:
            print("   chart unavailable — continuing without vision")

        # 3. RAG Query
        print("3. RAG Query (/query)...")
        rag_res_raw = requests.post("http://localhost:8001/query", json={"ticker": ticker, "question": question, "k": 4})
        rag_res_raw.raise_for_status()
        rag_res = rag_res_raw.json()
        
        # 4. Agentic Engine
        print("4. Agentic Engine (/synthesize)...")
        synth_payload = {
            "ticker": ticker,
            "question": question,
            "horizon_days": 30,
            "rag": {
                "summary": rag_res.get("summary"),
                "retrieved": rag_res.get("retrieved", [])
            },
            "vision": {
                "score": vision_res.get("score"),
                "label": vision_res.get("label"),
                "confidence": vision_res.get("confidence"),
                "patterns": vision_res.get("patterns", {}),
            } if vision_res else None,
        }
        synth_res_raw = requests.post("http://localhost:8003/synthesize", json=synth_payload)
        synth_res_raw.raise_for_status()
        synth_res = synth_res_raw.json()
        
        # 5. Guardrails Output
        print("5. Guardrails Output (/validate/output)...")
        out_payload = {
            "text": " ".join(synth_res.get("fundamental_view", {}).get("key_drivers", [])),
            "evidence": [d.get("text", "") for d in rag_res.get("retrieved", [])] + [rag_res.get("summary", "")]
        }
        out_res_raw = requests.post("http://localhost:8004/validate/output", json=out_payload)
        out_res_raw.raise_for_status()
        out_rail = out_res_raw.json()
        
        latency = time.perf_counter() - t0
        
        # Merge output rail info
        if out_rail.get("action") == "sanitize":
            synth_res.setdefault("caveats", []).append("Output rail sanitized certainty language in this report.")
        synth_res["output_rail"] = {"action": out_rail.get("action"), "violations": out_rail.get("violations")}
        
        print("\n✅ SUCCESS!")
        print(f"⏱️  Total Pipeline Latency: {latency:.2f}s")
        print("\n--- Final Probability Report ---")
        print(json.dumps(synth_res, indent=2))

    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e.response.status_code}")
        print(e.response.text)

if __name__ == "__main__":
    run_direct_e2e(sys.argv[1] if len(sys.argv) > 1 else "NVDA")
