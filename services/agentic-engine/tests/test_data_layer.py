"""Data-layer tests (Step 2b/2c) — macro snapshot, competitors, and the
MANDATORY macro/fear context injected into every analysis.

All market calls are mocked: no network, no rate-limit exposure. The fetchers
must degrade to {"error": ...} (never raise) so the desk is rate-limit safe.
"""
from __future__ import annotations

import pytest

from app import finance_tools, macro_context, orchestrator
from app.runs import build_run_store
from app.schemas import (
    AnalyzeRequest,
    FundamentalView,
    Probabilities,
    ProbabilityReport,
    RagInput,
    RiskAssessment,
    TechnicalView,
)


# ── yfinance fake ──────────────────────────────────────────────────────────────

class _FastInfo:
    def __init__(self, last, prev):
        self.last_price = last
        self.previous_close = prev


def _fake_ticker(price_map: dict, *, raise_for: set[str] = frozenset()):
    """Return a fake yfinance.Ticker class backed by `price_map` {sym: (last, prev)}."""
    class _FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        @property
        def fast_info(self):
            if self.sym in raise_for:
                raise RuntimeError("429 rate limited")
            last, prev = price_map.get(self.sym, (None, None))
            return _FastInfo(last, prev)

    return _FakeTicker


# ══════════════════════════════════════════════════════════════════════════════
# MACRO SNAPSHOT (S&P 500 / NASDAQ)
# ══════════════════════════════════════════════════════════════════════════════

def test_macro_snapshot_parses_indices(monkeypatch):
    pm = {"^GSPC": (5000.0, 4950.0), "^IXIC": (16000.0, 15800.0)}
    monkeypatch.setattr("yfinance.Ticker", _fake_ticker(pm))
    out = finance_tools.fetch_macro_snapshot()
    assert out["sp500"]["symbol"] == "^GSPC"
    assert out["sp500"]["change_pct"] == pytest.approx(1.01, abs=0.02)
    assert out["nasdaq"]["change_pct"] == pytest.approx(1.27, abs=0.05)
    assert out["market_tone"] == "risk-on (broad rally)"


def test_macro_snapshot_uses_etf_fallback(monkeypatch):
    """Index symbols missing → liquid ETF proxies are used."""
    pm = {"SPY": (500.0, 502.0), "QQQ": (430.0, 431.0)}
    monkeypatch.setattr("yfinance.Ticker", _fake_ticker(pm))
    out = finance_tools.fetch_macro_snapshot()
    assert out["sp500"]["symbol"] == "SPY"
    assert out["nasdaq"]["symbol"] == "QQQ"
    assert out["market_tone"] in ("flat / mixed", "mildly negative")


def test_macro_snapshot_no_crash_on_rate_limit(monkeypatch):
    """Every leg raising (e.g. 429) → graceful error dict, no exception."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        _fake_ticker({}, raise_for={"^GSPC", "SPY", "^IXIC", "QQQ"}),
    )
    out = finance_tools.fetch_macro_snapshot()
    assert "error" in out


# ══════════════════════════════════════════════════════════════════════════════
# COMPETITOR READ-THROUGH
# ══════════════════════════════════════════════════════════════════════════════

def test_competitors_for_nvda(monkeypatch):
    pm = {"AMD": (160.0, 158.0), "AVGO": (1700.0, 1680.0),
          "INTC": (30.0, 31.0), "TSM": (180.0, 178.0)}
    monkeypatch.setattr("yfinance.Ticker", _fake_ticker(pm))
    out = finance_tools.fetch_competitors("NVDA")
    assert out["ticker"] == "NVDA"
    peers = {p["ticker"] for p in out["peers"]}
    assert {"AMD", "AVGO", "INTC", "TSM"} <= peers
    amd = next(p for p in out["peers"] if p["ticker"] == "AMD")
    assert amd["change_pct"] == pytest.approx(1.27, abs=0.05)


def test_competitors_normalises_input(monkeypatch):
    monkeypatch.setattr("yfinance.Ticker", _fake_ticker({}))
    out = finance_tools.fetch_competitors("$nvda")
    assert out["ticker"] == "NVDA"


def test_competitors_unmapped_ticker_returns_empty(monkeypatch):
    out = finance_tools.fetch_competitors("ZZZZ")
    assert out["peers"] == []
    assert "no competitor mapping" in out["note"]


def test_competitors_resilient_per_peer(monkeypatch):
    """A single peer raising does not crash the whole read-through."""
    pm = {"AVGO": (1700.0, 1680.0), "INTC": (30.0, 31.0), "TSM": (180.0, 178.0)}
    monkeypatch.setattr("yfinance.Ticker", _fake_ticker(pm, raise_for={"AMD"}))
    out = finance_tools.fetch_competitors("NVDA")
    amd = next(p for p in out["peers"] if p["ticker"] == "AMD")
    assert amd["price"] is None and amd["change_pct"] is None


# ══════════════════════════════════════════════════════════════════════════════
# MANDATORY MACRO & FEAR CONTEXT (Req 2)
# ══════════════════════════════════════════════════════════════════════════════

def _good_macro():
    return {"sp500": {"symbol": "^GSPC", "price": 5000.0, "change_pct": 1.0},
            "nasdaq": {"symbol": "^IXIC", "price": 16000.0, "change_pct": 1.2},
            "market_tone": "risk-on (broad rally)"}


def _good_vix():
    return {"vix_30d": 14.5, "term_structure": "contango", "regime": "calm"}


def test_build_desk_context_is_mandatory_and_complete(monkeypatch):
    macro_context.reset_cache()
    monkeypatch.setattr(finance_tools, "fetch_macro_snapshot", _good_macro)
    monkeypatch.setattr(finance_tools, "fetch_vix_curve", _good_vix)
    block = macro_context.build_desk_context("AAPL")
    macro_context.reset_cache()
    assert "AAPL" in block
    assert "S&P 500" in block and "NASDAQ" in block
    assert "VIX" in block and "calm" in block


def test_build_desk_context_degrades_gracefully(monkeypatch):
    macro_context.reset_cache()
    monkeypatch.setattr(finance_tools, "fetch_macro_snapshot",
                        lambda: {"error": "broad-market data unavailable"})
    monkeypatch.setattr(finance_tools, "fetch_vix_curve",
                        lambda: {"error": "VIX term-structure data unavailable"})
    block = macro_context.build_desk_context("NVDA")
    macro_context.reset_cache()
    assert block and "NVDA" in block
    assert "Broad market: unavailable" in block
    assert "Fear index (VIX): unavailable" in block


def test_build_desk_context_caches_across_tickers(monkeypatch):
    """Market-wide data is fetched once per TTL — a 10-ticker burst = 1 fetch."""
    macro_context.reset_cache()
    calls = {"n": 0}

    def _counting_macro():
        calls["n"] += 1
        return _good_macro()

    monkeypatch.setattr(finance_tools, "fetch_macro_snapshot", _counting_macro)
    monkeypatch.setattr(finance_tools, "fetch_vix_curve", _good_vix)
    macro_context.build_desk_context("AAPL")
    macro_context.build_desk_context("MSFT")
    macro_context.build_desk_context("NVDA")
    macro_context.reset_cache()
    assert calls["n"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR WIRING — macro context present on EVERY analysis
# ══════════════════════════════════════════════════════════════════════════════

def _stub_report(sreq, run):
    return ProbabilityReport(
        run_id=run.run_id, ticker=sreq.ticker, question=sreq.question,
        horizon_days=sreq.horizon_days, generated_at="2026-06-18T00:00:00Z",
        probabilities=Probabilities(bullish=0.34, neutral=0.33, bearish=0.33),
        technical_view=TechnicalView(condition_score=0.0, rationale="x"),
        fundamental_view=FundamentalView(rationale="x"),
        risk_assessment=RiskAssessment(risk_level="low", max_position_pct=5.0),
        confidence=0.5, caveats=["paper only"], engine_backend="deterministic",
        execution_plan=None,
    )


def _wire_orchestrator(monkeypatch, captured):
    class _FakeEngine:
        name = "deterministic"

        def synthesize(self, sreq, run):
            captured["sreq"] = sreq
            return _stub_report(sreq, run)

    monkeypatch.setattr(orchestrator, "_validate_input", lambda req: (True, []))
    monkeypatch.setattr(orchestrator, "_rag_query", lambda req: RagInput(summary="s", retrieved=[]))
    monkeypatch.setattr(orchestrator, "_apply_output_rail", lambda report, rag, run: report)
    monkeypatch.setattr(orchestrator, "get_social_context", lambda t: "")
    monkeypatch.setattr("app.forecast.build_forecast", lambda *a, **k: None)
    return _FakeEngine()


def test_orchestrator_injects_mandatory_macro(monkeypatch):
    captured: dict = {}
    engine = _wire_orchestrator(monkeypatch, captured)
    monkeypatch.setattr("app.macro_context.build_desk_context", lambda t: "SENTINEL_MACRO_BLOCK")

    runs = build_run_store()
    run = runs.start("AAPL")
    orchestrator.run_analysis_job(
        AnalyzeRequest(ticker="AAPL", question="Outlook?", horizon_days=5),
        engine, run, runs,
    )
    assert "sreq" in captured, "engine.synthesize was never reached"
    assert captured["sreq"].macro_context == "SENTINEL_MACRO_BLOCK"


def test_orchestrator_respects_caller_supplied_macro(monkeypatch):
    captured: dict = {}
    engine = _wire_orchestrator(monkeypatch, captured)

    def _must_not_run(t):
        raise AssertionError("build_desk_context should not run when caller supplies macro")

    monkeypatch.setattr("app.macro_context.build_desk_context", _must_not_run)

    runs = build_run_store()
    run = runs.start("AAPL")
    orchestrator.run_analysis_job(
        AnalyzeRequest(ticker="AAPL", question="Outlook?", horizon_days=5,
                       macro_context="CALLER_BLOCK"),
        engine, run, runs,
    )
    assert captured["sreq"].macro_context == "CALLER_BLOCK"
