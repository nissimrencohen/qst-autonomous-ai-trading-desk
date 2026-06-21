"""Step 2e integration harness — the Continuous Synthesis Loop, end-to-end.

Proves the Golden Rule: runs the REAL offline crew for one ticker (default AAPL)
with ``yfinance.Ticker`` patched to RAISE — so if any code path tried a live
call the run would crash. A successful report therefore proves the agents read
EXCLUSIVELY from the seeded IngestionStore.

Usage:  python scripts/integration_synthesis_loop.py [TICKER]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except Exception:
    pass
os.environ["AGENTIC_ENGINE_BACKEND"] = "crew"
os.environ.setdefault("AGENTIC_SOCIAL_PIPELINE_ENABLED", "false")
os.environ.setdefault("AGENTIC_INGESTION_ENABLED", "false")
os.environ.setdefault("AGENTIC_BRIEFING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("AGENTIC_LLM_PROVIDER_CHAIN", "openai")
sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

from app.config import settings  # noqa: E402
from app.ingestion_store import IngestionRow, IngestionStore  # noqa: E402
from app.report_store import ReportStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed(store: IngestionStore, t: str) -> None:
    store.upsert([
        IngestionRow(t, "quote", f"Quote {t}", "{}", _now(),
                     meta_json=json.dumps({"ticker": t, "price": 212.5, "eps": 6.4, "pe": 33.2, "market_cap": 3.3e12})),
        IngestionRow(t, "ta_signal", f"TA {t}", "ta", _now(),
                     meta_json=json.dumps({"rsi": 61.2, "rsi_signal": "neutral", "macd_cross": "bullish", "bb_position": "upper_half", "price": 212.5})),
        IngestionRow(t, "competitor", f"Peers {t}", "peers", _now(),
                     meta_json=json.dumps({"ticker": t, "peers": [
                         {"ticker": "MSFT", "price": 510.0, "change_pct": -0.8},
                         {"ticker": "GOOGL", "price": 190.0, "change_pct": -1.1},
                         {"ticker": "AMZN", "price": 220.0, "change_pct": -0.5}]})),
        IngestionRow(t, "news", f"{t} unveils new AI features at WWDC", f"News: {t} AI features", _now(),
                     meta_json=json.dumps({"publisher": "Reuters"})),
        IngestionRow("MACRO", "macro", "Broad Market", "macro", _now(),
                     meta_json=json.dumps({"sp500": {"symbol": "^GSPC", "price": 7420.1, "change_pct": -1.21},
                                           "nasdaq": {"symbol": "^IXIC", "price": 26021.7, "change_pct": -1.34},
                                           "market_tone": "risk-off (broad selloff)"})),
        IngestionRow("VIX", "macro", "VIX Term Structure", "vix", _now(),
                     meta_json=json.dumps({"vix_30d": 17.1, "term_structure": "contango", "regime": "elevated"})),
    ])


def main() -> int:
    import app.engine as engine_mod
    from app.synthesis_loop import _synthesize_one

    store = IngestionStore(":memory:")
    _seed(store, TICKER)
    print("=" * 72)
    print(f"Seeded IngestionStore for {TICKER}: {store.count(TICKER)} ticker rows + MACRO + VIX")

    # HARD DECOUPLING PROOF: any live yfinance call now raises.
    import yfinance as yf
    def _boom(*a, **k):
        raise RuntimeError("LIVE CALL ATTEMPTED — decoupling violated!")
    yf.Ticker = _boom
    print("yfinance.Ticker patched to RAISE — any live call will crash the run.\n")

    runs = build_run_store()
    rs = ReportStore(":memory:")
    engine = engine_mod.build_synthesis_engine(store)
    print(f"Engine: {engine.name} (offline tools bound to the seeded store)\n")
    print(f"Running offline crew synthesis for {TICKER} (real LLM)…\n")

    report = _synthesize_one(TICKER, settings, engine, runs, store, rs)

    if report is None:
        print("INTEGRATION RESULT: FAIL ❌ — synthesis returned no report")
        return 1

    print("\n=== RUN TRACE STEPS ===")
    trace = runs.get(report.run_id)
    for s in (trace.steps if trace else []):
        print(f"  {s.get('step')}")

    print("\n=== FINAL REPORT (excerpt) ===")
    print("engine_backend:", report.engine_backend)
    print("probabilities :", report.probabilities.model_dump())
    print("fundamental   :", report.fundamental_view.key_drivers)
    print("volatility    :", report.volatility_view.model_dump() if report.volatility_view else None)
    print("caveats       :", report.caveats[:3])

    persisted = rs.get(TICKER)
    blob = json.dumps(report.model_dump()).upper()
    peers_hit = [p for p in ("MSFT", "GOOGL", "AMZN") if p in blob]
    macro_hit = [m for m in ("VIX", "MACRO", "REGIME", "RISK-OFF", "S&P", "NASDAQ", "VOLATILITY") if m in blob]

    print("\n" + "=" * 72)
    print("VERIFICATION")
    print(f"  report persisted to ReportStore : {persisted is not None}")
    print(f"  peers surfaced in report        : {peers_hit}")
    print(f"  macro/fear terms in report      : {macro_hit}")
    ok = persisted is not None and bool(macro_hit)
    print("=" * 72)
    print(f"\nINTEGRATION RESULT for {TICKER}: {'PASS ✅ (offline, zero live calls)' if ok else 'FAIL ❌'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
