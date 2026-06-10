"""Synthesis tests (deterministic engine — no LLM calls)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

BULLISH_VISION = {
    "score": 0.82,
    "label": "bullish",
    "confidence": 0.9,
    "patterns": {"breakout_up": 0.8, "consolidation": 0.1},
}

NVDA_RAG = {
    "summary": (
        "- Nvidia reported data-center revenue of $41.2B for Q1-2026, up 38% "
        "year over year. [source: Nvidia Q1-2026 results]\n"
        "- Open interest in NVDA June call options at the 1300 strike rose 4x. "
        "[source: NVDA options flow ahead of June expiry]\n"
        "Coverage: Nvidia Q1-2026 results, NVDA options flow ahead of June expiry"
    ),
    "retrieved": [
        {
            "id": "NVDA-1", "title": "Nvidia Q1-2026 results",
            "source": "Q1-2026 earnings call", "published_at": "2026-05-21",
            "text": "Nvidia reported data-center revenue of $41.2B...",
        },
        {
            "id": "NVDA-2", "title": "NVDA options flow ahead of June expiry",
            "source": "options desk note", "published_at": "2026-05-12",
            "text": "Open interest in NVDA June call options...",
        },
    ],
}

CUE_RAG = {
    "summary": (
        "- Cue Biopharma released interim Phase 1b data for CUE-101 with an "
        "overall response rate of 36%. [source: CUE Phase 1b update]\n"
        "Coverage: CUE Phase 1b update"
    ),
    "retrieved": [
        {
            "id": "CUE-1", "title": "CUE Phase 1b update",
            "source": "clinical update", "published_at": "2026-05-19",
            "text": "Interim Phase 1b data...",
        }
    ],
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _synth(client: TestClient, **overrides):
    payload = {
        "ticker": "NVDA",
        "question": "What is the probability of upside into June expiry?",
        "horizon_days": 30,
        "rag": NVDA_RAG,
        "vision": BULLISH_VISION,
        **overrides,
    }
    return client.post("/synthesize", json=payload)


def test_report_contract(client: TestClient) -> None:
    res = _synth(client)
    assert res.status_code == 200
    report = res.json()
    probs = report["probabilities"]
    assert abs(sum(probs.values()) - 1.0) < 0.01
    assert report["ticker"] == "NVDA"
    assert report["engine_backend"] == "deterministic"
    assert report["caveats"], "caveats must never be empty"
    assert report["fundamental_view"]["sources"] == [
        "Nvidia Q1-2026 results", "NVDA options flow ahead of June expiry",
    ]


def test_bullish_vision_tilts_probabilities(client: TestClient) -> None:
    report = _synth(client).json()
    assert report["probabilities"]["bullish"] > report["probabilities"]["bearish"]
    assert "breakout_up" in report["technical_view"]["dominant_patterns"]


def test_missing_vision_degrades_gracefully(client: TestClient) -> None:
    report = _synth(client, vision=None).json()
    probs = report["probabilities"]
    assert probs["bullish"] == pytest.approx(probs["bearish"], abs=0.01)
    assert "No technical confirmation available." in report["risk_assessment"]["key_risks"]


def test_binary_catalyst_caps_position(client: TestClient) -> None:
    report = _synth(client, ticker="CUE", rag=CUE_RAG,
                    question="How risky is the Phase 1b readout?").json()
    assert report["risk_assessment"]["risk_level"] == "high"
    assert report["risk_assessment"]["max_position_pct"] <= 2.0


def test_run_trace_is_retrievable(client: TestClient) -> None:
    report = _synth(client).json()
    res = client.get(f"/runs/{report['run_id']}")
    assert res.status_code == 200
    trace = res.json()
    steps = [s["step"] for s in trace["steps"]]
    assert steps == ["technical_analysis", "fundamental_analysis", "risk_synthesis"]
    assert trace["finished_at"] is not None


def test_unknown_run_returns_404(client: TestClient) -> None:
    assert client.get("/runs/doesnotexist").status_code == 404
