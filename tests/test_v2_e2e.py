"""v2 desk E2E — 6 parallel agents + Quant Execution Manager.

Usage: python test_v2_e2e.py [TICKER] [--vol]
Defaults to SPCX. Prints probabilities + execution_plan + volatility/space views.
"""
import json
import sys
import time

import requests

TICKER = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "SPCX"
VOL = "--vol" in sys.argv
QUESTION = (
    f"Provide a full technical, fundamental, volatility, options-flow and "
    f"space-economy read on {TICKER}, and give a paper execution plan."
)

t0 = time.perf_counter()

print(f"=== v2 E2E :: {TICKER} (volatility_desk={VOL}) ===")
g = requests.post("http://localhost:8004/validate/input",
                  json={"ticker": TICKER, "question": QUESTION, "source": "e2e_v2"}).json()
print(f"1. input rail: allowed={g.get('allowed')}")
if not g.get("allowed"):
    sys.exit("blocked by input rail")

rag = requests.post("http://localhost:8001/query",
                    json={"ticker": TICKER, "question": QUESTION, "k": 4}).json()
print(f"2. rag: summary_len={len(rag.get('summary') or '')} docs={len(rag.get('retrieved', []))}")

print("3. synthesize (6 parallel agents)… this can take a few minutes")
payload = {
    "ticker": TICKER,
    "question": QUESTION,
    "horizon_days": 30,
    "volatility_desk": VOL,
    "rag": {"summary": rag.get("summary"), "retrieved": rag.get("retrieved", [])},
    "vision": None,
}
res = requests.post("http://localhost:8003/synthesize", json=payload, timeout=600)
latency = time.perf_counter() - t0

if res.status_code != 200:
    print(f"\nFAILED {res.status_code}: {res.text[:500]}")
    sys.exit(1)

rep = res.json()
p = rep["probabilities"]
print(f"\nSUCCESS  latency={latency:.1f}s  engine={rep['engine_backend']}")
print(f"probabilities: bull={p['bullish']} neutral={p['neutral']} bear={p['bearish']}")
print(f"confidence={rep['confidence']}  risk={rep['risk_assessment']['risk_level']}")

ep = rep.get("execution_plan")
print("\n--- QUANT EXECUTION (paper) ---")
print(json.dumps(ep, indent=2) if ep else "  (none)")

vv = rep.get("volatility_view")
print("\n--- VOLATILITY VIEW ---")
print(json.dumps(vv, indent=2) if vv else "  (none)")

sv = rep.get("space_economy_view")
print("\n--- SPACE ECONOMY VIEW ---")
print(json.dumps(sv, indent=2) if sv else "  (none)")

print("\n--- fundamental drivers ---")
for d in rep["fundamental_view"]["key_drivers"]:
    print(f"  • {d}")
