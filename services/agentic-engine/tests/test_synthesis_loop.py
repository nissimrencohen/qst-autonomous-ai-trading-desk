"""Step 2e tests — offline tools (decoupling), store-backed macro context,
report store, and the synthesis-loop single-ticker runner.

The headline guarantee is DECOUPLING: with `yfinance.Ticker` patched to raise,
the offline tools and the loop still produce results purely from the SQLite
ingestion cache.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import offline_tools, macro_context
from app.config import settings
from app.engine import build_synthesis_engine, DeterministicEngine
from app.ingestion_store import IngestionRow, IngestionStore
from app.report_store import ReportStore
from app.runs import build_run_store
from app.synthesis_loop import _build_rag_from_store, _synthesize_one


def _now_iso(offset_min: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat(timespec="seconds")


@pytest.fixture()
def seeded_store():
    """An in-memory IngestionStore populated with one full cycle for AAPL."""
    store = IngestionStore(":memory:")
    rows = [
        IngestionRow("AAPL", "quote", "Quote: AAPL", "{}", _now_iso(),
                     meta_json=json.dumps({"ticker": "AAPL", "price": 212.5, "eps": 6.4, "pe": 33.2, "market_cap": 3.3e12})),
        IngestionRow("AAPL", "ta_signal", "TA: AAPL", "ta", _now_iso(),
                     meta_json=json.dumps({"rsi": 61.2, "rsi_signal": "neutral", "macd_cross": "bullish", "bb_position": "upper_half", "price": 212.5})),
        IngestionRow("AAPL", "competitor", "Peers: AAPL", "peers", _now_iso(),
                     meta_json=json.dumps({"ticker": "AAPL", "peers": [{"ticker": "MSFT", "price": 510.0, "change_pct": -0.8}, {"ticker": "GOOGL", "price": 190.0, "change_pct": -1.1}]})),
        IngestionRow("AAPL", "news", "Apple unveils new AI features", "News body", _now_iso(),
                     meta_json=json.dumps({"publisher": "Reuters"})),
        IngestionRow("MACRO", "macro", "Broad Market", "macro", _now_iso(),
                     meta_json=json.dumps({"sp500": {"symbol": "^GSPC", "price": 7420.1, "change_pct": -1.21}, "nasdaq": {"symbol": "^IXIC", "price": 26021.7, "change_pct": -1.34}, "market_tone": "risk-off (broad selloff)"})),
        IngestionRow("VIX", "macro", "VIX Term Structure", "vix", _now_iso(),
                     meta_json=json.dumps({"vix_30d": 17.1, "term_structure": "contango", "regime": "elevated"})),
    ]
    store.upsert(rows)
    yield store
    store.close()


# ══════════════════════════════════════════════════════════════════════════════
# OFFLINE TOOLS — read from cache, never from the network
# ══════════════════════════════════════════════════════════════════════════════

class TestOfflineTools:

    def test_quote_from_cache(self, seeded_store):
        q = offline_tools.offline_market_quote(seeded_store, "AAPL")
        assert q["price"] == 212.5 and q["eps"] == 6.4

    def test_quote_falls_back_to_ta_price(self):
        store = IngestionStore(":memory:")
        store.upsert([IngestionRow("NVDA", "ta_signal", "TA", "ta", _now_iso(),
                                   meta_json=json.dumps({"price": 175.0}))])
        q = offline_tools.offline_market_quote(store, "NVDA")
        assert q["price"] == 175.0 and "not in cache" in q["note"]
        store.close()

    def test_vix_and_macro_from_cache(self, seeded_store):
        assert offline_tools.offline_vix_curve(seeded_store)["regime"] == "elevated"
        assert offline_tools.offline_macro_snapshot(seeded_store)["market_tone"].startswith("risk-off")

    def test_competitors_from_cache(self, seeded_store):
        peers = {p["ticker"] for p in offline_tools.offline_competitor_analysis(seeded_store, "AAPL")["peers"]}
        assert {"MSFT", "GOOGL"} <= peers

    def test_unmapped_sources_report_unavailable_no_live_call(self, seeded_store):
        assert "not available" in offline_tools._OPTIONS_UNAVAILABLE["error"]
        assert "not available" in offline_tools._LAUNCH_UNAVAILABLE["error"]

    def test_missing_ticker_returns_error_dict(self, seeded_store):
        assert "error" in offline_tools.offline_market_quote(seeded_store, "TQQQ")

    def test_decoupling_yfinance_never_called(self, seeded_store, monkeypatch):
        """Even if yfinance would explode, offline tools serve from cache."""
        def _boom(*a, **k):
            raise RuntimeError("yfinance must not be called in continuous mode")
        monkeypatch.setattr("yfinance.Ticker", _boom)
        assert offline_tools.offline_market_quote(seeded_store, "AAPL")["price"] == 212.5
        assert offline_tools.offline_vix_curve(seeded_store)["regime"] == "elevated"
        assert offline_tools.offline_competitor_analysis(seeded_store, "AAPL")["peers"]

    def test_build_offline_tools_registers_expected_names(self, seeded_store):
        tools = offline_tools.build_offline_finance_tools(seeded_store)
        names = {getattr(t, "name", "") for t in tools}
        assert {"get_market_quote", "get_vix_curve", "get_macro_snapshot",
                "get_competitor_analysis", "get_technical_indicators"} <= names


# ══════════════════════════════════════════════════════════════════════════════
# STORE-BACKED MACRO/FEAR CONTEXT (Pillar 3)
# ══════════════════════════════════════════════════════════════════════════════

class TestStoreMacroContext:

    def test_context_complete_from_store(self, seeded_store):
        block = macro_context.build_desk_context_from_store(seeded_store, "AAPL")
        assert "AAPL" in block
        assert "S&P 500" in block and "NASDAQ" in block
        assert "VIX" in block and "elevated" in block

    def test_context_flags_stale_data(self):
        store = IngestionStore(":memory:")
        store.upsert([
            IngestionRow("MACRO", "macro", "m", "m", _now_iso(-120), ingested_at=_now_iso(-120),
                         meta_json=json.dumps({"sp500": {"price": 1, "change_pct": 0}, "nasdaq": {"price": 1, "change_pct": 0}, "market_tone": "flat"})),
            IngestionRow("VIX", "macro", "v", "v", _now_iso(-120), ingested_at=_now_iso(-120),
                         meta_json=json.dumps({"vix_30d": 15, "term_structure": "contango", "regime": "calm"})),
        ])
        block = macro_context.build_desk_context_from_store(store, "NVDA", stale_minutes=15)
        assert "stale" in block.lower()
        store.close()

    def test_context_degrades_when_empty(self):
        store = IngestionStore(":memory:")
        block = macro_context.build_desk_context_from_store(store, "AAPL")
        assert block and "unavailable" in block
        store.close()


# ══════════════════════════════════════════════════════════════════════════════
# REPORT STORE (Pillar 4)
# ══════════════════════════════════════════════════════════════════════════════

class TestReportStore:

    def test_save_get_roundtrip(self):
        rs = ReportStore(":memory:")
        rs.save("AAPL", {"ticker": "AAPL", "generated_at": "2026-06-18T00:00:00Z", "probabilities": {"bullish": 0.4}}, "run123")
        got = rs.get("AAPL")
        assert got["run_id"] == "run123" and got["report"]["probabilities"]["bullish"] == 0.4
        rs.close()

    def test_upsert_overwrites_latest(self):
        rs = ReportStore(":memory:")
        rs.save("NVDA", {"ticker": "NVDA", "v": 1}, "r1")
        rs.save("NVDA", {"ticker": "NVDA", "v": 2}, "r2")
        assert rs.get("NVDA")["report"]["v"] == 2
        assert len(rs.get_all()) == 1
        rs.close()

    def test_cursor_and_heartbeat(self):
        rs = ReportStore(":memory:")
        assert rs.get_cursor() == 0
        rs.set_cursor(4)
        assert rs.get_cursor() == 4
        rs.record_heartbeat("AAPL", "done")
        st = rs.status()
        assert st["last_ticker"] == "AAPL" and st["last_status"] == "done" and st["heartbeat"]
        rs.close()

    def test_last_seen(self):
        rs = ReportStore(":memory:")
        assert rs.last_seen("AAPL") is None
        rs.mark_seen("AAPL", "2026-06-18T10:00:00+00:00")
        assert rs.last_seen("aapl") == "2026-06-18T10:00:00+00:00"
        rs.close()


# ══════════════════════════════════════════════════════════════════════════════
# SYNTHESIS LOOP — single-ticker runner (deterministic engine, no network)
# ══════════════════════════════════════════════════════════════════════════════

class TestSynthesisLoopRunner:

    def test_build_rag_from_store(self, seeded_store):
        rag = _build_rag_from_store(seeded_store, "AAPL", news_limit=5)
        assert rag.retrieved and "Apple unveils new AI features" in rag.summary

    def test_build_rag_empty_when_no_news(self):
        store = IngestionStore(":memory:")
        rag = _build_rag_from_store(store, "TQQQ", news_limit=5)
        assert rag.retrieved == [] and "does not cover" in rag.summary
        store.close()

    def test_build_synthesis_engine_deterministic_in_tests(self, seeded_store):
        # conftest pins AGENTIC_ENGINE_BACKEND=deterministic
        engine = build_synthesis_engine(seeded_store)
        assert isinstance(engine, DeterministicEngine)

    def test_synthesize_one_persists_report(self, seeded_store, monkeypatch):
        monkeypatch.setattr("app.orchestrator._apply_output_rail", lambda report, rag, run: report)
        runs = build_run_store()
        rs = ReportStore(":memory:")
        engine = DeterministicEngine()
        report = _synthesize_one("AAPL", settings, engine, runs, seeded_store, rs)
        assert report is not None and report.ticker == "AAPL"
        # persisted to the report store + run store
        assert rs.get("AAPL")["report"]["ticker"] == "AAPL"
        assert runs.get(report.run_id) is not None
        rs.close()

    def test_synthesize_one_persists_macro_block(self, seeded_store, monkeypatch):
        """Step 2g data integrity: the saved payload carries the macro/VIX block."""
        monkeypatch.setattr("app.orchestrator._apply_output_rail", lambda report, rag, run: report)
        runs = build_run_store()
        rs = ReportStore(":memory:")
        _synthesize_one("AAPL", settings, DeterministicEngine(), runs, seeded_store, rs)
        saved = rs.get("AAPL")
        assert saved["macro"]["macro"]["market_tone"].startswith("risk-off")
        assert saved["macro"]["vix"]["regime"] == "elevated"
        rs.close()

    def test_synthesize_one_decoupled_from_yfinance(self, seeded_store, monkeypatch):
        """The loop builds context purely from the store — yfinance untouched."""
        def _boom(*a, **k):
            raise RuntimeError("no live calls allowed in continuous mode")
        monkeypatch.setattr("yfinance.Ticker", _boom)
        monkeypatch.setattr("app.orchestrator._apply_output_rail", lambda report, rag, run: report)
        runs = build_run_store()
        rs = ReportStore(":memory:")
        report = _synthesize_one("AAPL", settings, DeterministicEngine(), runs, seeded_store, rs)
        assert report is not None and report.ticker == "AAPL"
        # macro/fear from the store made it into the run trace context step
        steps = {s["step"] for s in runs.get(report.run_id).steps}
        assert "synthesis_loop_context" in steps
        rs.close()
