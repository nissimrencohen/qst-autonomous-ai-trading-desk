"""One-shot REAL run (no demo seed, no recurring loop).

1. Runs ONE real ingestion cycle for all 10 tickers (live yfinance: quote, news,
   TA, competitors + market-wide macro & VIX).
2. Runs the offline crew ONCE per ticker (real LLM agents reading the ingested
   cache) and persists each report to the ReportStore.

The continuous loop is NOT started — this executes a single full pass and exits.
Then start the backend (loop disabled) pointing at the same DBs to serve the UI.

Usage:  python scripts/run_once_real.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "services" / "agentic-engine" / "data"
_DATA.mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except Exception:
    pass

# Real crew via OpenAI (Groq daily cap avoided → fewer 429 retries, faster).
os.environ["AGENTIC_ENGINE_BACKEND"] = "crew"
os.environ["AGENTIC_LLM_PROVIDER_CHAIN"] = "openai"
os.environ["AGENTIC_INGESTION_DB_PATH"] = str(_DATA / "real_ingestion.db")
os.environ["AGENTIC_SYNTHESIS_REPORT_DB_PATH"] = str(_DATA / "real_synthesis_reports.db")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "sqlite")
os.environ.setdefault("AGENTIC_SOCIAL_PIPELINE_ENABLED", "false")
os.environ.setdefault("AGENTIC_BRIEFING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

from app.config import settings  # noqa: E402
from app.engine import build_synthesis_engine  # noqa: E402
from app.ingestion_engine import _run_ingestion_cycle  # noqa: E402
from app.ingestion_store import IngestionStore  # noqa: E402
from app.report_store import ReportStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402
from app.synthesis_loop import _synthesize_one  # noqa: E402
from app.watchlist import WATCHLIST_ORDERED  # noqa: E402

_PEERS = ["AMD", "AVGO", "INTC", "TSM", "MSFT", "GOOGL", "AMZN", "META", "ORCL",
          "RKLB", "ASTS", "LMT", "BA", "WMT", "BABA", "UVXY", "VXX", "VIXM", "SVIX", "SPY", "QQQ"]


def main() -> int:
    print("=" * 72, flush=True)
    print("ONE-SHOT REAL RUN — engine=crew via OpenAI", flush=True)
    print(f"ingestion db: {settings.ingestion_db_path}", flush=True)
    print(f"report db   : {settings.synthesis_report_db_path}", flush=True)
    print("=" * 72, flush=True)

    store = IngestionStore(settings.ingestion_db_path)

    # ── 1. Real ingestion (one cycle) ────────────────────────────────────────
    print("\n[1/2] INGESTION — collecting REAL data for 10 tickers…", flush=True)
    t0 = time.time()
    asyncio.run(_run_ingestion_cycle(settings, store))
    by_type: dict[str, int] = {}
    for t in (*WATCHLIST_ORDERED, "MACRO", "VIX"):
        for st in ("quote", "news", "ta_signal", "competitor", "macro", "tavily_news"):
            rows = store.query_latest(t, st, 50)
            if rows:
                by_type[st] = by_type.get(st, 0) + len(rows)
    print(f"      ingestion done in {time.time()-t0:.0f}s · rows by type: {by_type} · total={store.count()}", flush=True)

    # ── 2. Real crew synthesis, once per ticker ──────────────────────────────
    print("\n[2/2] SYNTHESIS — real offline crew, once per ticker…", flush=True)
    engine = build_synthesis_engine(store)
    runs = build_run_store()
    rs = ReportStore(settings.synthesis_report_db_path)

    for i, ticker in enumerate(WATCHLIST_ORDERED, 1):
        ts = time.time()
        print(f"   ({i}/10) {ticker} …", flush=True)
        rep = _synthesize_one(ticker, settings, engine, runs, store, rs)
        if rep is None:
            print(f"        {ticker}: FAILED", flush=True)
            continue
        blob = json.dumps(rep.model_dump()).upper()
        peers = sorted({p for p in _PEERS if p in blob and p != ticker})
        print(f"        {ticker}: backend={rep.engine_backend} "
              f"p_bull={rep.probabilities.bullish:.2f} risk={rep.risk_assessment.risk_level} "
              f"peers={peers} took {time.time()-ts:.0f}s", flush=True)

    # ── summary ──────────────────────────────────────────────────────────────
    allr = rs.get_all()
    print("\n" + "=" * 72, flush=True)
    print(f"DONE — {len(allr)}/10 reports persisted. status={rs.status()}", flush=True)
    print("=" * 72, flush=True)
    store.close()
    rs.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
