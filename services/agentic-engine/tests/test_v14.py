"""v1.4 unit tests — Gatekeeper, Batch Orchestrator, Market Data, Auth, PgRunStore interface.

All tests run without external dependencies:
- Gatekeeper: pure whitelist logic, no broker call (Alpaca key absent)
- Batch: mocked run_analysis_job, tests semaphore + run_id generation
- Market data: provider chain logic with monkeypatched providers
- Auth: JWT encode/decode, disabled-mode no-op
- PgRunStore interface: tested via the in-memory RunStore (same contract)
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

# ── ensure deterministic engine (no LLM) ──────────────────────────────────────
os.environ.setdefault("AGENTIC_ENGINE_BACKEND", "deterministic")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "memory")
os.environ.setdefault("AGENTIC_RUN_STORE_BACKEND", "memory")
os.environ.setdefault("AGENTIC_AUTH_ENABLED", "false")
os.environ.setdefault("AGENTIC_AUTH_ADMIN_PASSWORD", "admin")
os.environ.setdefault("AGENTIC_AUTH_USER_PASSWORD", "user")
# Fresh, isolated users DB so the seed always runs with the passwords above.
import tempfile as _tempfile  # noqa: E402
os.environ.setdefault(
    "AGENTIC_USERS_DB_PATH", os.path.join(_tempfile.mkdtemp(), "users_test.db")
)

from app.main import app  # noqa: E402 — env must be set before import
from app.config import settings  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _minimal_report():
    """Minimal valid ProbabilityReport dict (flat=no execution plan)."""
    from app.schemas import (
        FundamentalView, Probabilities, ProbabilityReport,
        RiskAssessment, TechnicalView,
    )
    return ProbabilityReport(
        run_id="test000",
        ticker="NVDA",
        question="test",
        horizon_days=30,
        generated_at="2026-06-17T00:00:00Z",
        probabilities=Probabilities(bullish=0.5, neutral=0.3, bearish=0.2),
        technical_view=TechnicalView(condition_score=0.5, rationale="ok"),
        fundamental_view=FundamentalView(rationale="ok"),
        risk_assessment=RiskAssessment(risk_level="low", max_position_pct=5.0),
        confidence=0.7,
        caveats=["paper only"],
        engine_backend="deterministic",
        execution_plan=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GATEKEEPER
# ══════════════════════════════════════════════════════════════════════════════

class TestGatekeeperWhitelist:

    def test_whitelisted_tickers_pass(self):
        from app.gatekeeper import is_whitelisted
        for t in ["SPCX", "MSFT", "AAPL", "NVDA", "GOOGL",
                  "AMZN", "UPRO", "TQQQ", "VIXY", "SVXY"]:
            assert is_whitelisted(t), f"{t} should be whitelisted"

    def test_vix_aliases_no_longer_whitelisted(self):
        """V2.0 strict watchlist: bare VIX proxies are NOT tradeable members.
        The fear index is analysed via mandatory macro/VIX context, not as a
        standalone whitelisted ticker."""
        from app.gatekeeper import is_whitelisted
        for t in ["VIX", "^VIX", "vix", "VXX", "UVXY"]:
            assert not is_whitelisted(t), f"{t} should NOT be whitelisted"

    def test_non_whitelisted_blocked(self):
        from app.gatekeeper import is_whitelisted
        for t in ["TSLA", "META", "SPY", "QQQ", "ESLT", "NXSN", "CUE"]:
            assert not is_whitelisted(t), f"{t} should NOT be whitelisted"

    def test_dollar_prefix_stripped(self):
        from app.gatekeeper import is_whitelisted
        assert is_whitelisted("$NVDA")
        assert is_whitelisted("$AAPL")

    def test_enforce_flat_plan_passes_through(self):
        """No execution plan → gatekeeper is a no-op."""
        from app.gatekeeper import enforce
        report = _minimal_report()
        gk = enforce(report, "run001")
        assert gk.execution_allowed is True
        assert gk.order is None
        assert gk.violation_reasons == []

    def test_enforce_blocks_non_whitelisted_execution(self):
        from app.gatekeeper import enforce
        from app.schemas import ExecutionPlan
        report = _minimal_report().model_copy(update={
            "ticker": "TSLA",
            "execution_plan": ExecutionPlan(side="long", entry=250.0, target=270.0, stop_loss=240.0),
        })
        gk = enforce(report, "run002")
        assert gk.execution_allowed is False
        assert len(gk.violation_reasons) > 0
        assert any("TSLA" in r for r in gk.violation_reasons)
        assert any("GATEKEEPER" in c for c in gk.report.caveats)

    def test_enforce_allows_whitelisted_ticker_stub_broker(self):
        """NVDA is whitelisted; no Alpaca key → stub order."""
        from app.gatekeeper import enforce
        from app.schemas import ExecutionPlan
        report = _minimal_report().model_copy(update={
            "ticker": "NVDA",
            "execution_plan": ExecutionPlan(side="long", entry=200.0, target=220.0, stop_loss=190.0),
        })
        gk = enforce(report, "run003")
        assert gk.execution_allowed is True
        assert gk.order is not None
        assert gk.order.status in ("stub", "submitted")  # stub when no key
        assert gk.order.ticker == "NVDA"

    def test_gatekeeper_whitelist_endpoint(self, client: TestClient):
        res = client.get("/gatekeeper/whitelist")
        assert res.status_code == 200
        wl = res.json()["whitelist"]
        assert "NVDA" in wl
        assert "AAPL" in wl
        assert "VIXY" in wl   # added in V2.0
        assert "UVXY" not in wl  # dropped in V2.0
        assert len(wl) == 10


# ══════════════════════════════════════════════════════════════════════════════
# STRICT INPUT VALIDATION (V2.0 Req 1 — reject off-list tickers immediately)
# ══════════════════════════════════════════════════════════════════════════════

class TestStrictTickerValidation:

    def test_analyze_rejects_off_list_ticker(self, client: TestClient):
        res = client.post("/analyze", json={
            "ticker": "TSLA", "question": "Outlook?", "horizon_days": 5,
        })
        assert res.status_code == 422
        assert "watchlist" in res.text.lower()

    def test_synthesize_rejects_off_list_ticker(self, client: TestClient):
        res = client.post("/synthesize", json={
            "ticker": "CUE", "question": "Phase 1b readout risk?",
            "horizon_days": 30, "rag": {"summary": None, "retrieved": []},
        })
        assert res.status_code == 422

    def test_analyze_accepts_new_watchlist_members(self, client: TestClient):
        for t in ["UPRO", "TQQQ", "VIXY"]:
            res = client.post("/analyze", json={
                "ticker": t, "question": "Outlook?", "horizon_days": 5,
            })
            assert res.status_code == 200, f"{t} should be accepted"

    def test_dollar_prefix_and_case_normalised(self, client: TestClient):
        """'$aapl' is accepted and normalised to canonical 'AAPL'."""
        res = client.post("/analyze", json={
            "ticker": "$aapl", "question": "Outlook?", "horizon_days": 5,
        })
        assert res.status_code == 200
        run_id = res.json()["run_id"]
        trace = client.get(f"/runs/{run_id}").json()
        assert trace["ticker"] == "AAPL"


# ══════════════════════════════════════════════════════════════════════════════
# BATCH ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class TestBatchOrchestrator:

    def test_batch_endpoint_returns_run_ids(self, client: TestClient):
        res = client.post("/analyze/batch", json={
            "tickers": ["NVDA", "AAPL"],
            "question": "What is the short-term outlook?",
            "horizon_days": 5,
        })
        assert res.status_code == 200
        body = res.json()
        assert "started" in body
        assert "skipped" in body
        started_tickers = {r["ticker"] for r in body["started"]}
        assert "NVDA" in started_tickers
        assert "AAPL" in started_tickers
        for r in body["started"]:
            assert len(r["run_id"]) == 12

    def test_batch_skips_non_whitelisted(self, client: TestClient):
        res = client.post("/analyze/batch", json={
            "tickers": ["NVDA", "TSLA", "META"],
            "question": "Outlook?",
            "horizon_days": 5,
        })
        assert res.status_code == 200
        body = res.json()
        started_tickers = {r["ticker"] for r in body["started"]}
        skipped_tickers = {r["ticker"] for r in body["skipped"]}
        assert "NVDA" in started_tickers
        assert "TSLA" in skipped_tickers
        assert "META" in skipped_tickers

    def test_batch_all_non_whitelisted(self, client: TestClient):
        res = client.post("/analyze/batch", json={
            "tickers": ["TSLA", "META"],
            "question": "Outlook?",
            "horizon_days": 5,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["started"] == []
        assert len(body["skipped"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketData:

    def test_chain_falls_back_to_yfinance_when_no_keys(self, monkeypatch):
        """With no Polygon/Alpaca keys, chain falls through to yfinance."""
        from app import market_data
        from app.config import settings

        _yf_result = {"ticker": "NVDA", "price": 200.0, "currency": "USD",
                      "name": "Test", "eps": None, "pe": None,
                      "market_cap": None, "fifty_two_week_high": None,
                      "fifty_two_week_low": None, "_source": "yfinance"}

        # _PROVIDERS is a module-level dict with references to the functions;
        # patch the dict entries directly so fetch_quote_resilient sees the mocks.
        monkeypatch.setitem(market_data._PROVIDERS, "polygon", lambda t: None)
        monkeypatch.setitem(market_data._PROVIDERS, "alpaca", lambda t: None)
        monkeypatch.setitem(market_data._PROVIDERS, "yfinance", lambda t: _yf_result)
        monkeypatch.setattr(settings, "market_data_chain", "polygon,alpaca,yfinance")

        result = market_data.fetch_quote_resilient("NVDA")
        assert result["_source"] == "yfinance"
        assert result["price"] == 200.0

    def test_polygon_used_when_available(self, monkeypatch):
        from app import market_data
        from app.config import settings

        _poly_result = {"ticker": "AAPL", "price": 180.0, "_source": "polygon",
                        "currency": "USD", "name": None, "eps": None,
                        "pe": None, "market_cap": None,
                        "fifty_two_week_high": None, "fifty_two_week_low": None}

        monkeypatch.setitem(market_data._PROVIDERS, "polygon", lambda t: _poly_result)
        monkeypatch.setitem(market_data._PROVIDERS, "alpaca", lambda t: None)
        monkeypatch.setitem(market_data._PROVIDERS, "yfinance", lambda t: None)
        monkeypatch.setattr(settings, "market_data_chain", "polygon,alpaca,yfinance")

        result = market_data.fetch_quote_resilient("AAPL")
        assert result["_source"] == "polygon"

    def test_all_providers_fail_returns_error(self, monkeypatch):
        from app import market_data
        from app.config import settings

        monkeypatch.setitem(market_data._PROVIDERS, "polygon", lambda t: None)
        monkeypatch.setitem(market_data._PROVIDERS, "alpaca", lambda t: None)
        monkeypatch.setitem(market_data._PROVIDERS, "yfinance", lambda t: None)
        monkeypatch.setattr(settings, "market_data_chain", "polygon,alpaca,yfinance")

        result = market_data.fetch_quote_resilient("FAKE")
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_auth_disabled_analyze_requires_no_token(self, client: TestClient):
        """When auth is disabled (default), /analyze works without a token."""
        res = client.post("/analyze", json={
            "ticker": "NVDA",
            "question": "Short-term outlook?",
            "horizon_days": 5,
        })
        assert res.status_code == 200
        assert "run_id" in res.json()

    def test_token_endpoint_exists(self, client: TestClient):
        """POST /auth/token endpoint is always mounted."""
        res = client.post("/auth/token", data={
            "username": "admin",
            "password": "wrong",
        })
        # 503 when no admin password set, or 401 when wrong — either way, endpoint exists
        assert res.status_code in (401, 503)

    def test_require_auth_noop_when_disabled(self):
        """require_auth returns None (no-op) when AUTH_ENABLED=false."""
        from app.auth import require_auth
        result = require_auth(token=None)
        assert result is None

    def test_jwt_encode_decode_roundtrip(self):
        """JWT token encodes the subject + role and decodes correctly."""
        from app.auth import _make_token, _decode_token
        from app.config import settings

        if not settings.auth_secret.get_secret_value() or \
           settings.auth_secret.get_secret_value().startswith("changeme"):
            pytest.skip("AUTH_SECRET not configured for JWT test")

        token = _make_token("admin", "admin")
        assert isinstance(token, str)
        claims = _decode_token(token)
        assert claims["sub"] == "admin"
        assert claims["role"] == "admin"

    def test_db_login_returns_role(self, client: TestClient):
        """DB-backed login: seeded admin/user return a token + their role."""
        admin_pw = settings.auth_admin_password.get_secret_value() or "admin"
        res = client.post("/auth/token", data={"username": "admin", "password": admin_pw})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["role"] == "admin"
        assert body["token_type"] == "bearer"
        assert body["access_token"]

        user_pw = settings.auth_user_password.get_secret_value() or "user"
        res2 = client.post("/auth/token", data={"username": "user", "password": user_pw})
        assert res2.status_code == 200, res2.text
        assert res2.json()["role"] == "user"

    def test_require_admin_blocks_standard_user(self, client: TestClient):
        """The require_admin dependency raises 403 for a 'user' role token."""
        from app.auth import _make_token, require_admin
        from fastapi import HTTPException

        # admin passes
        assert require_admin({"username": "admin", "role": "admin"})["role"] == "admin"
        # standard user is rejected
        with pytest.raises(HTTPException) as exc:
            require_admin({"username": "user", "role": "user"})
        assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# RUN STORE INTERFACE (in-memory — same contract as PgRunStore)
# ══════════════════════════════════════════════════════════════════════════════

class TestRunStoreInterface:

    def test_build_run_store_returns_memory_by_default(self):
        from app.runs import build_run_store, RunStore
        store = build_run_store()
        assert isinstance(store, RunStore)

    def test_run_lifecycle(self):
        from app.runs import build_run_store
        store = build_run_store()
        handle = store.start("NVDA")
        assert len(handle.run_id) == 12

        handle.log("step_a", {"value": 42})
        trace = store.get(handle.run_id)
        assert trace is not None
        assert trace.status == "running"
        assert any(s["step"] == "step_a" for s in trace.steps)

        store.finish(handle.run_id)
        trace = store.get(handle.run_id)
        assert trace.finished_at is not None

    def test_set_blocked(self):
        from app.runs import build_run_store
        store = build_run_store()
        handle = store.start("TSLA")
        store.set_blocked(handle.run_id, ["not whitelisted"])
        trace = store.get(handle.run_id)
        assert trace.status == "blocked"
        assert "not whitelisted" in trace.blocked_reasons

    def test_set_error(self):
        from app.runs import build_run_store
        store = build_run_store()
        handle = store.start("AAPL")
        store.set_error(handle.run_id, "something broke")
        trace = store.get(handle.run_id)
        assert trace.status == "error"
        assert trace.error == "something broke"

    def test_unknown_run_id_returns_none(self):
        from app.runs import build_run_store
        store = build_run_store()
        assert store.get("nonexistent") is None
