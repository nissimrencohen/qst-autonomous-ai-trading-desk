"""Bug #1 verification — Daily Briefing must NOT error when rag-service is down.

rag-service and guardrails-service are NOT started here. Before the fix the
briefing fired the live orchestrator (RAG /query over HTTP) → every instrument
came back status:"error", crew bull/neut/bear = 0/null. After the offline
migration it should generate cleanly for all 10 tickers from the ingestion cache.

(The unrelated yfinance market-data helpers — gaps / 30-min bars / move-probs —
are monkeypatched to no-ops so the run is fast and isolated to the crew path.)

Usage:  python scripts/verify_bug1_briefing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
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

# Deterministic engine = fast, no LLM cost; the bug is the rag/orchestrator
# dependency, not the engine. (A crew run works too, just slower.)
os.environ["AGENTIC_ENGINE_BACKEND"] = "deterministic"
os.environ["AGENTIC_INGESTION_DB_PATH"] = str(_DATA / "real_ingestion.db")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "memory")
os.environ.setdefault("AGENTIC_RUN_STORE_BACKEND", "memory")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
# Point HTTP deps at a dead port to PROVE the briefing no longer needs them.
os.environ["AGENTIC_RAG_URL"] = "http://127.0.0.1:59999"
os.environ["AGENTIC_GUARDRAILS_URL"] = "http://127.0.0.1:59999"
sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

import app.daily_briefing as db  # noqa: E402
from app.briefing_store import BriefingStore  # noqa: E402
from app.runs import build_run_store  # noqa: E402

# Stub the unrelated yfinance market-data helpers (not the bug; keeps it fast).
db._fetch_overnight_gaps = lambda: {}
db._fetch_30min_data = lambda: {}
db.instrument_probs = lambda **k: {}
db.vix_implied_probs = lambda *a, **k: {}


async def main() -> int:
    runs = build_run_store()
    bstore = BriefingStore(str(_DATA / "verify_briefing.db"))
    print("Running OFFLINE briefing with rag-service + guardrails pointed at a DEAD port…\n")
    b = await db.run_daily_briefing(None, runs, bstore)

    insts = b["instruments"]
    statuses = Counter(i["status"] for i in insts)
    errors = [i["ticker"] for i in insts if i["status"] != "done"]

    print(f"engine_backend : {b['engine_backend']}   data_source: {b.get('data_source')}")
    print(f"instruments    : {len(insts)}   statuses: {dict(statuses)}")
    print("sample crew signals:")
    for i in insts[:4]:
        c = i["crew"]
        print(f"  {i['ticker']:5s} status={i['status']:5s} "
              f"bull={c['bullish']} neut={c['neutral']} bear={c['bearish']} risk={c['risk_level']}")
    print(f"\nerrors: {errors or 'NONE'}")
    ok = len(insts) == 10 and not errors and all(i["crew"]["neutral"] is not None for i in insts)
    print(f"RESULT: {'PASS ✅ — briefing generated for all 10, zero status:error' if ok else 'FAIL ❌'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
