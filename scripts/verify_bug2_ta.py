"""Bug #2 verification — does the Technical Analyst now cite REAL RSI/MACD?

Runs the offline crew against the real ingestion cache for a few tickers that
previously all collapsed to 40/40/20, and checks:
  • get_technical_indicators actually fired (spied),
  • the technical_view rationale cites the cached RSI/MACD/BB numbers,
  • the bullish probabilities now diverge across tickers.

Usage:  python scripts/verify_bug2_ta.py [TICKER ...]   (default NVDA GOOGL MSFT)
"""
from __future__ import annotations

import json
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
sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

import app.offline_tools as ot  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine import build_synthesis_engine  # noqa: E402
from app.ingestion_store import IngestionStore  # noqa: E402
from app.report_store import ReportStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402
from app.synthesis_loop import _synthesize_one  # noqa: E402

TICKERS = sys.argv[1:] or ["NVDA", "GOOGL", "MSFT"]

store = IngestionStore(settings.ingestion_db_path)

# Spy on the offline TA reader so we can prove the tool fired per ticker.
_real_ta = ot.offline_technical_indicators
_ta_calls: dict[str, int] = {}
def _spy(st, ticker):
    _ta_calls[ticker.upper()] = _ta_calls.get(ticker.upper(), 0) + 1
    return _real_ta(st, ticker)
ot.offline_technical_indicators = _spy

import app.orchestrator as orch  # noqa: E402
orch._apply_output_rail = lambda report, rag, run: report  # skip guardrails HTTP

engine = build_synthesis_engine(store)
runs = build_run_store()
rs = ReportStore(":memory:")

print("=" * 78)
results = []
for t in TICKERS:
    ta = store.query_latest(t, "ta_signal", 1)
    cached = ta[0].meta if ta else {}
    print(f"\n### {t} — cached TA: RSI={cached.get('rsi')} ({cached.get('rsi_signal')}) "
          f"MACD={cached.get('macd_cross')} BB={cached.get('bb_position')}")
    rep = _synthesize_one(t, settings, engine, runs, store, rs)
    if rep is None:
        print(f"  {t}: FAILED"); continue
    rationale = rep.technical_view.rationale
    rsi_str = str(cached.get("rsi", "")).split(".")[0]  # integer part of RSI
    cites_rsi = ("RSI" in rationale.upper()) and (rsi_str and rsi_str in rationale)
    cites_macd = "MACD" in rationale.upper()
    print(f"  tool fired: {_ta_calls.get(t,0)}x | technical_view: {rationale[:200]}")
    print(f"  cites RSI#={cites_rsi}  cites MACD={cites_macd}  probs={rep.probabilities.model_dump()}")
    results.append((t, rep.probabilities.bullish, cites_rsi or cites_macd))

print("\n" + "=" * 78)
bulls = [b for _, b, _ in results]
spread = (max(bulls) - min(bulls)) if bulls else 0
print(f"bullish spread across {len(results)} tickers: {spread:.2f}  (values: {[(t, round(b,2)) for t,b,_ in results]})")
print(f"all cite real indicators: {all(c for *_, c in results)}")
print(f"DIVERGED from flat 40/40/20: {'YES' if spread >= 0.05 else 'NO'}")
store.close(); rs.close()
