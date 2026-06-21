"""Bug #3 verification — DeterministicEngine must tilt from cached TA (no flatline).

Runs the DETERMINISTIC offline synthesis for all 10 tickers against the real
ingestion cache and prints the probability spread. Proves we can run meaningful
UI/dashboard tests with zero LLM tokens.

Usage:  python scripts/verify_bug3_deterministic.py
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
os.environ["AGENTIC_ENGINE_BACKEND"] = "deterministic"
os.environ["AGENTIC_INGESTION_DB_PATH"] = str(_DATA / "real_ingestion.db")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "memory")
os.environ.setdefault("AGENTIC_RUN_STORE_BACKEND", "memory")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

from app.config import settings  # noqa: E402
from app.engine import build_synthesis_engine  # noqa: E402
from app.ingestion_store import IngestionStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402
from app.watchlist import WATCHLIST_ORDERED  # noqa: E402
import app.orchestrator as orch  # noqa: E402
import app.synthesis_loop as sl  # noqa: E402

orch._apply_output_rail = lambda report, rag, run: report  # skip guardrails HTTP

store = IngestionStore(settings.ingestion_db_path)
engine = build_synthesis_engine(store)   # DeterministicEngine
runs = build_run_store()

print(f"engine: {engine.name}\n")
print(f"{'TKR':5s} {'RSI':>5s} {'MACD':8s} {'BB':12s} | {'BULL':>5s} {'NEUT':>5s} {'BEAR':>5s} {'tilt':>6s} {'risk':6s}")
print("-" * 72)
bulls = []
for t in WATCHLIST_ORDERED:
    ta = store.query_latest(t, "ta_signal", 1)
    m = ta[0].meta if ta else {}
    rep = sl.synthesize_ticker_offline(t, settings, engine, runs, store)
    if rep is None:
        print(f"{t:5s} FAILED"); continue
    p = rep.probabilities
    bulls.append(p.bullish)
    print(f"{t:5s} {str(m.get('rsi')):>5s} {str(m.get('macd_cross')):8s} {str(m.get('bb_position')):12s} | "
          f"{p.bullish:5.2f} {p.neutral:5.2f} {p.bearish:5.2f} {rep.technical_view.condition_score:+6.2f} "
          f"{rep.risk_assessment.risk_level:6s}")

spread = (max(bulls) - min(bulls)) if bulls else 0.0
flat = all(abs(b - 0.3333) < 0.01 for b in bulls)
print("-" * 72)
print(f"bullish range: {min(bulls):.2f} … {max(bulls):.2f}   spread = {spread:.2f}")
print(f"RESULT: {'FAIL ❌ still flat 33/33/33' if flat else 'PASS ✅ — probabilities tilt with the cached TA'}")
store.close()
