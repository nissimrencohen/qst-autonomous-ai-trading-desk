"""Component tests for the 1-minute continuous ingestion engine (Step 2d).

All tests use in-memory SQLite and mocked yfinance/requests — no network,
no rate limits, fully deterministic. Run with:

    cd services/agentic-engine
    ../../.venv/Scripts/python.exe -m pytest tests/test_ingestion.py -v
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion_store import IngestionRow, IngestionStore, _compute_id
from app.ingestion_engine import (
    _compute_bollinger,
    _compute_macd,
    _compute_rsi,
    _ingest_competitors,
    _ingest_macro,
    _ingest_news,
    _ingest_ta_signals,
    _ingest_tavily_news,
    _run_ingestion_cycle,
)


# ═══════════════════════════════════════════════════════════════════════════════
# IngestionStore tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIngestionStore:
    """SQLite store: upsert, dedup, query, prune."""

    def _make_row(self, ticker="AAPL", source="news", title="t", body="b",
                  published_at="2026-06-18T10:00:00+00:00", **kw) -> IngestionRow:
        return IngestionRow(
            ticker=ticker, source_type=source, title=title, body=body,
            published_at=published_at, **kw,
        )

    def test_upsert_dedup(self):
        """Inserting the same 5 rows twice yields count=5 total (dedup works)."""
        store = IngestionStore(":memory:")
        rows = [self._make_row(title=f"headline-{i}", body=f"body-{i}") for i in range(5)]
        assert store.upsert(rows) == 5   # first insert
        assert store.upsert(rows) == 0   # deduped
        assert store.count() == 5

    def test_query_latest(self):
        """query_latest returns rows in descending ingested_at order."""
        store = IngestionStore(":memory:")
        now = datetime.now(timezone.utc)
        rows = []
        for i in range(10):
            ts = (now - timedelta(minutes=10 - i)).isoformat(timespec="seconds")
            rows.append(self._make_row(
                title=f"h-{i}", body=f"b-{i}", ingested_at=ts,
            ))
        store.upsert(rows)

        latest_3 = store.query_latest("AAPL", "news", limit=3)
        assert len(latest_3) == 3
        # Most recent first
        assert latest_3[0].title == "h-9"
        assert latest_3[1].title == "h-8"
        assert latest_3[2].title == "h-7"

    def test_query_since(self):
        """query_since filters by ingested_at correctly."""
        store = IngestionStore(":memory:")
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=2)).isoformat(timespec="seconds")
        new_ts = (now - timedelta(minutes=5)).isoformat(timespec="seconds")

        store.upsert([
            self._make_row(title="old", body="old-body", ingested_at=old_ts),
            self._make_row(title="new", body="new-body", ingested_at=new_ts),
        ])

        since_1h = now - timedelta(hours=1)
        results = store.query_since("AAPL", since_1h)
        assert len(results) == 1
        assert results[0].title == "new"

    def test_prune(self):
        """prune removes rows older than the threshold."""
        store = IngestionStore(":memory:")
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=50)).isoformat(timespec="seconds")
        new_ts = now.isoformat(timespec="seconds")

        store.upsert([
            self._make_row(title="ancient", body="a", ingested_at=old_ts),
            self._make_row(title="fresh", body="f", ingested_at=new_ts),
        ])
        assert store.count() == 2
        deleted = store.prune(older_than_hours=48)
        assert deleted == 1
        assert store.count() == 1
        assert store.query_latest("AAPL")[0].title == "fresh"

    def test_count_by_ticker(self):
        """count(ticker) only counts rows for that ticker."""
        store = IngestionStore(":memory:")
        store.upsert([
            self._make_row(ticker="AAPL", title="a1", body="b1"),
            self._make_row(ticker="AAPL", title="a2", body="b2"),
            self._make_row(ticker="NVDA", title="n1", body="nb1"),
        ])
        assert store.count("AAPL") == 2
        assert store.count("NVDA") == 1
        assert store.count() == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Fetcher tests (mocked yfinance)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIngestNews:
    """News fetcher: yfinance.Ticker mocked."""

    def test_ingest_news_mocked(self):
        mock_news = [
            {"title": "Apple beats earnings", "publisher": "Reuters",
             "link": "https://reuters.com/apple", "providerPublishTime": 1718700000},
            {"title": "Apple AI launch", "publisher": "Bloomberg",
             "link": "https://bloomberg.com/apple", "providerPublishTime": 1718703600},
        ]
        mock_ticker = MagicMock()
        mock_ticker.news = mock_news

        with patch("yfinance.Ticker", return_value=mock_ticker):
            rows = _ingest_news("AAPL")

        assert len(rows) == 2
        assert rows[0].ticker == "AAPL"
        assert rows[0].source_type == "news"
        assert "Apple beats earnings" in rows[0].title
        assert "Reuters" in rows[0].body

    def test_ingest_news_exception_safe(self):
        """yfinance failure returns empty list, never raises."""
        with patch("yfinance.Ticker", side_effect=Exception("network timeout")):
            rows = _ingest_news("AAPL")
        assert rows == []


class TestIngestTASignals:
    """TA signal fetcher: RSI, MACD, Bollinger from mocked 5m bars."""

    def _make_hist(self, n=40, base_price=150.0):
        """Create a mock DataFrame with Close column."""
        import pandas as pd
        import numpy as np
        np.random.seed(42)
        closes = base_price + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({"Close": closes})

    def test_ingest_ta_signals_mocked(self):
        hist = self._make_hist(40, 150.0)
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("yfinance.Ticker", return_value=mock_ticker):
            rows = _ingest_ta_signals("NVDA")

        assert len(rows) == 1
        assert rows[0].ticker == "NVDA"
        assert rows[0].source_type == "ta_signal"
        meta = json.loads(rows[0].meta_json)
        assert "rsi" in meta
        assert "macd" in meta
        assert "bb_position" in meta
        assert meta["rsi_signal"] in ("overbought", "oversold", "neutral")
        assert meta["macd_cross"] in ("bullish", "bearish", "neutral")

    def test_ingest_ta_signals_insufficient_data(self):
        """With <15 bars, returns empty list gracefully."""
        import pandas as pd
        hist = pd.DataFrame({"Close": [100.0] * 10})
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        with patch("yfinance.Ticker", return_value=mock_ticker):
            rows = _ingest_ta_signals("AAPL")
        assert rows == []


class TestIngestMacro:
    """Macro fetcher: uses the existing cached fetch_macro_snapshot/fetch_vix_curve."""

    def test_ingest_macro_returns_rows(self):
        with patch("app.finance_tools.fetch_macro_snapshot") as mock_macro, \
             patch("app.finance_tools.fetch_vix_curve") as mock_vix:
            mock_macro.return_value = {
                "sp500": {"symbol": "^GSPC", "price": 5500.0, "change_pct": 0.42},
                "nasdaq": {"symbol": "^IXIC", "price": 18000.0, "change_pct": 0.65},
                "avg_change_pct": 0.54, "market_tone": "risk-on",
            }
            mock_vix.return_value = {
                "vix_30d": 16.5, "vix_3m": 18.0, "vix_9d": 15.2,
                "term_structure": "contango", "regime": "calm",
            }

            rows = _ingest_macro()

        assert len(rows) == 2  # one macro + one VIX
        assert rows[0].ticker == "MACRO"
        assert rows[1].ticker == "VIX"
        assert rows[0].source_type == "macro"

    def test_ingest_macro_error_safe(self):
        """Errors in macro data return empty list."""
        with patch("app.finance_tools.fetch_macro_snapshot") as mock_macro, \
             patch("app.finance_tools.fetch_vix_curve") as mock_vix:
            mock_macro.return_value = {"error": "unavailable"}
            mock_vix.return_value = {"error": "unavailable"}
            rows = _ingest_macro()
        assert rows == []


class TestIngestTavily:
    """Tavily news fetcher: mocked HTTP."""

    def test_ingest_tavily_news_mocked(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"title": "AAPL surges on AI", "content": "Apple stock rose...",
                 "url": "https://example.com/aapl", "score": 0.95},
            ]
        }
        with patch("app.ingestion_engine.requests.post", return_value=mock_resp):
            rows = _ingest_tavily_news("AAPL", "fake-key")

        assert len(rows) == 1
        assert rows[0].source_type == "tavily_news"
        assert "AAPL surges on AI" in rows[0].title

    def test_ingest_tavily_rate_limited(self):
        """429 returns empty list, no crash."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with patch("app.ingestion_engine.requests.post", return_value=mock_resp):
            rows = _ingest_tavily_news("AAPL", "fake-key")
        assert rows == []


# ═══════════════════════════════════════════════════════════════════════════════
# TA indicator unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTAIndicators:
    """Pure-Python TA calculations."""

    def test_rsi_neutral_range(self):
        """Random walk should produce RSI near 50."""
        import random
        random.seed(42)
        closes = [100.0]
        for _ in range(50):
            closes.append(closes[-1] + random.gauss(0, 1))
        rsi = _compute_rsi(closes, 14)
        assert 20 < rsi < 80  # not extreme

    def test_rsi_all_gains(self):
        """Monotonically rising closes -> RSI = 100."""
        closes = list(range(100, 120))
        rsi = _compute_rsi(closes, 14)
        assert rsi == 100.0

    def test_macd_returns_three_values(self):
        closes = [100 + i * 0.5 for i in range(30)]
        macd_line, signal_line, histogram = _compute_macd(closes)
        assert isinstance(macd_line, float)
        assert isinstance(signal_line, float)
        assert isinstance(histogram, float)

    def test_bollinger_bands_structure(self):
        closes = [100.0 + i for i in range(25)]
        upper, mid, lower = _compute_bollinger(closes, period=20, num_std=2)
        assert upper > mid > lower
        assert mid == pytest.approx(sum(closes[-20:]) / 20, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Full cycle integration (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullCycleMocked:
    """Run a full ingestion cycle with all yfinance/HTTP calls mocked."""

    @pytest.fixture
    def mock_cfg(self):
        cfg = MagicMock()
        cfg.ingestion_interval_s = 60
        cfg.ingestion_db_path = ":memory:"
        cfg.ingestion_concurrency = 3
        cfg.ingestion_prune_hours = 48
        cfg.ingestion_tavily_interval_s = 1800
        cfg.tavily_api_key.get_secret_value.return_value = ""
        cfg.rag_url = "http://rag-service:8001"
        return cfg

    def test_full_cycle_mocked(self, mock_cfg):
        """Full cycle: all fetchers mocked, rows land in SQLite."""
        import pandas as pd
        import numpy as np

        store = IngestionStore(":memory:")
        np.random.seed(42)
        mock_hist = pd.DataFrame({"Close": 150 + np.cumsum(np.random.randn(40) * 0.5)})

        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"title": "Test headline", "publisher": "Test",
             "link": "http://test.com", "providerPublishTime": 1718700000}
        ]
        mock_ticker.history.return_value = mock_hist

        with patch("yfinance.Ticker", return_value=mock_ticker), \
             patch("app.finance_tools.fetch_macro_snapshot") as mock_macro, \
             patch("app.finance_tools.fetch_vix_curve") as mock_vix, \
             patch("app.finance_tools.fetch_competitors") as mock_comp, \
             patch("app.ingestion_engine.requests"):

            mock_macro.return_value = {
                "sp500": {"symbol": "^GSPC", "price": 5500, "change_pct": 0.5},
                "nasdaq": {"symbol": "^IXIC", "price": 18000, "change_pct": 0.6},
                "avg_change_pct": 0.55, "market_tone": "risk-on",
            }
            mock_vix.return_value = {
                "vix_30d": 16, "vix_3m": 18,
                "term_structure": "contango", "regime": "calm",
            }
            mock_comp.return_value = {
                "ticker": "AAPL", "peers": [
                    {"ticker": "MSFT", "price": 430, "change_pct": 0.3},
                ]
            }

            asyncio.run(_run_ingestion_cycle(mock_cfg, store))

        # Should have data: 2 macro + (10 tickers x news + ta + competitor)
        total = store.count()
        assert total > 0, f"Expected rows in store, got {total}"
        # At minimum: 2 macro + 10 news + 10 TA + 10 competitor = 32
        assert total >= 20  # conservative bound (dedup may reduce)

    def test_rate_limit_resilience(self, mock_cfg):
        """One ticker's yfinance failure doesn't block the others."""
        import pandas as pd
        import numpy as np

        store = IngestionStore(":memory:")
        np.random.seed(42)
        mock_hist = pd.DataFrame({"Close": 150 + np.cumsum(np.random.randn(40) * 0.5)})

        def flaky_ticker(sym):
            if sym == "SPCX":
                raise Exception("HTTP 429: rate limited")
            mock = MagicMock()
            mock.news = [{"title": f"News for {sym}", "publisher": "T",
                          "link": "", "providerPublishTime": 0}]
            mock.history.return_value = mock_hist
            return mock

        with patch("yfinance.Ticker", side_effect=flaky_ticker), \
             patch("app.finance_tools.fetch_macro_snapshot") as mock_macro, \
             patch("app.finance_tools.fetch_vix_curve") as mock_vix, \
             patch("app.finance_tools.fetch_competitors") as mock_comp, \
             patch("app.ingestion_engine.requests"):

            mock_macro.return_value = {"error": "skip"}
            mock_vix.return_value = {"error": "skip"}
            mock_comp.return_value = {"ticker": "X", "peers": []}

            asyncio.run(_run_ingestion_cycle(mock_cfg, store))

        # SPCX should fail but others should succeed
        total = store.count()
        assert total > 0, "Other tickers should have been processed despite SPCX failure"
