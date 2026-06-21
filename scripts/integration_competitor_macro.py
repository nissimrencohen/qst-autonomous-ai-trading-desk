"""Step 2f / Step 4 integration harness.

Runs the REAL CrewAI desk for ONE ticker (default AAPL) and verifies that:
  1. an analyst actively CALLED get_competitor_analysis (spied), and
  2. the final ProbabilityReport DISCUSSES the macro & fear backdrop.

Isolated from the sibling services: feeds a stubbed RAG briefing + the mandatory
macro context directly into CrewEngine.synthesize (no rag/vision HTTP needed).
Requires LLM keys + AGENTIC_ENGINE_BACKEND=crew (loaded from the repo .env).

Usage:  python scripts/integration_competitor_macro.py [TICKER]
Exit 0 on success (both checks pass), 1 otherwise.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# LLM/macro output may contain non-ASCII; force UTF-8 stdout on Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_REPO = Path(__file__).resolve().parents[1]

# ── env: load repo .env and force the crew engine BEFORE importing app ────────
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except Exception:
    pass
os.environ["AGENTIC_ENGINE_BACKEND"] = "crew"
os.environ.setdefault("AGENTIC_SOCIAL_PIPELINE_ENABLED", "false")
os.environ.setdefault("AGENTIC_BRIEFING_SCHEDULER_ENABLED", "false")

sys.path.insert(0, str(_REPO / "services" / "agentic-engine"))

import app.finance_tools as ft  # noqa: E402
from app.engine import CrewEngine  # noqa: E402
from app.macro_context import build_desk_context, reset_cache  # noqa: E402
from app.runs import build_run_store  # noqa: E402
from app.schemas import RagInput, RetrievedDocIn, SynthesizeRequest  # noqa: E402

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()

# ── spies (delegate to the real fetchers, record invocations) ─────────────────
_real_comp = ft.fetch_competitors
_comp_calls: list[str] = []


def _spy_comp(ticker, *a, **k):
    res = _real_comp(ticker, *a, **k)
    _comp_calls.append(ticker)
    print(f"[TOOL CALL] get_competitor_analysis({ticker!r}) -> peers="
          f"{[p.get('ticker') for p in res.get('peers', [])]}")
    return res


ft.fetch_competitors = _spy_comp

_real_macro = ft.fetch_macro_snapshot
_macro_calls: list[int] = []


def _spy_macro(*a, **k):
    _macro_calls.append(1)
    return _real_macro(*a, **k)


ft.fetch_macro_snapshot = _spy_macro

# ── build the mandatory macro context + a realistic RAG stub ──────────────────
reset_cache()
macro = build_desk_context(TICKER)
print("=" * 72)
print("MANDATORY MACRO & FEAR CONTEXT injected into the crew:")
print(macro)
print("=" * 72)

rag = RagInput(
    summary=(
        f"- {TICKER} posted record services revenue, up double digits YoY. "
        f"[source: {TICKER} Q2-2026 results]\n"
        f"- Unit growth was flat; the Street's focus is the AI product roadmap. "
        f"[source: {TICKER} earnings call]\n"
        f"Coverage: {TICKER} Q2-2026 results, {TICKER} earnings call"
    ),
    retrieved=[
        RetrievedDocIn(id=f"{TICKER}-1", title=f"{TICKER} Q2-2026 results",
                       source="earnings", published_at="2026-05-01",
                       text="Record services revenue; margins steady."),
        RetrievedDocIn(id=f"{TICKER}-2", title=f"{TICKER} earnings call",
                       source="call", published_at="2026-05-01",
                       text="Flat units; AI roadmap in focus."),
    ],
)

req = SynthesizeRequest(
    ticker=TICKER,
    question=(f"What is the {TICKER} 30-day upside probability given the macro "
              f"backdrop and its competitive position versus peers?"),
    horizon_days=30,
    rag=rag,
    vision=None,
    macro_context=macro,
)

# ── run the real crew ─────────────────────────────────────────────────────────
runs = build_run_store()
run = runs.start(TICKER)
print(f"\nRunning CrewEngine.synthesize for {TICKER} (real LLM calls)…\n")
engine = CrewEngine()
report = engine.synthesize(req, run)

# ── trace + report excerpt ────────────────────────────────────────────────────
trace = runs.get(run.run_id)
print("\n=== RUN TRACE STEPS ===")
for s in (trace.steps if trace else []):
    print(f"  {s.get('step'):18s} {json.dumps(s.get('data', {}))[:120]}")

print("\n=== FINAL REPORT (excerpt) ===")
print("probabilities :", report.probabilities.model_dump())
print("technical     :", report.technical_view.rationale)
print("fundamental   :", report.fundamental_view.key_drivers)
print("risk notes    :", report.risk_assessment.notes)
print("volatility    :", report.volatility_view.model_dump() if report.volatility_view else None)
print("caveats       :", report.caveats)

# ── verification ──────────────────────────────────────────────────────────────
blob = json.dumps(report.model_dump()).upper()
peer_universe = ["AMD", "AVGO", "INTC", "TSM", "MSFT", "GOOGL", "AMZN", "META",
                 "ORCL", "RKLB", "ASTS", "LMT", "BA", "WMT", "BABA"]
peers_in_report = sorted({p for p in peer_universe if p in blob and p != TICKER})
macro_terms = sorted({t for t in [
    "VIX", "MACRO", "S&P", "NASDAQ", "REGIME", "FEAR", "BROAD MARKET",
    "RISK-ON", "RISK-OFF", "CONTANGO", "BACKWARDATION", "VOLATILITY",
] if t in blob})

competitor_tool_called = len(_comp_calls) > 0
macro_discussed = len(macro_terms) > 0

print("\n" + "=" * 72)
print("VERIFICATION")
print(f"  get_competitor_analysis calls : {_comp_calls}  -> {'PASS' if competitor_tool_called else 'FAIL'}")
print(f"  get_macro_snapshot fetches     : {len(_macro_calls)}")
print(f"  peer tickers in final report   : {peers_in_report}")
print(f"  macro/fear terms in report     : {macro_terms}  -> {'PASS' if macro_discussed else 'FAIL'}")
print("=" * 72)

ok = competitor_tool_called and macro_discussed
print(f"\nINTEGRATION RESULT for {TICKER}: {'PASS ✅' if ok else 'FAIL ❌'}")
sys.exit(0 if ok else 1)
