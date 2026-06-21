"""Bug #4 (part 2) verification — does the LLM manager break the 40/40/20 anchor?

Runs the real offline crew for a strong-bull name, a bearish name, and a middling
one, and checks whether the manager now pushes conviction (favored > 0.60 and/or
opposing < 0.15) instead of reflexively returning ~40/40/20.

Usage:  python scripts/verify_bug4_calibration.py [TICKER ...]   (default AMZN VIXY NVDA)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "services" / "agentic-engine" / "data"
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except Exception:
    pass
os.environ["AGENTIC_ENGINE_BACKEND"] = "crew"
os.environ["AGENTIC_LLM_PROVIDER_CHAIN"] = "openai"
os.environ["AGENTIC_INGESTION_DB_PATH"] = str(_DATA / "real_ingestion.db")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "memory")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

from app.config import settings  # noqa: E402
from app.engine import build_synthesis_engine  # noqa: E402
from app.ingestion_store import IngestionStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402
import app.orchestrator as orch  # noqa: E402
import app.synthesis_loop as sl  # noqa: E402

orch._apply_output_rail = lambda report, rag, run: report

TICKERS = sys.argv[1:] or ["AMZN", "VIXY", "NVDA"]
store = IngestionStore(settings.ingestion_db_path)
engine = build_synthesis_engine(store)
runs = build_run_store()

print(f"engine: {engine.name}\n")
broke = 0
for t in TICKERS:
    ta = store.query_latest(t, "ta_signal", 1)
    m = ta[0].meta if ta else {}
    rep = sl.synthesize_ticker_offline(t, settings, engine, runs, store)
    if rep is None:
        print(f"{t}: FAILED"); continue
    p = rep.probabilities
    fav, opp = max(p.bullish, p.bearish), min(p.bullish, p.bearish)
    anchor_broken = fav >= 0.60 or opp <= 0.15 or p.neutral <= 0.25
    broke += anchor_broken
    print(f"{t:5s} TA[RSI={m.get('rsi')} MACD={m.get('macd_cross')} BB={m.get('bb_position')}] "
          f"-> bull={p.bullish:.2f} neut={p.neutral:.2f} bear={p.bearish:.2f}  "
          f"{'BROKE ANCHOR ✅' if anchor_broken else 'still ~flat'}")

print(f"\nRESULT: {broke}/{len(TICKERS)} broke the 40/40/20 anchor "
      f"({'PASS ✅' if broke >= 1 else 'FAIL ❌'})")
store.close()
