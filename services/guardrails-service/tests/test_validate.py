"""Input/output rail tests (rule backend — deterministic, no LLM)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _input(client: TestClient, question: str, ticker: str | None = None):
    return client.post(
        "/validate/input", json={"question": question, "ticker": ticker}
    ).json()


def _output(client: TestClient, text: str, evidence: list[str] = []):
    return client.post(
        "/validate/output", json={"text": text, "evidence": evidence}
    ).json()


# ---- input rail -------------------------------------------------------------

def test_legitimate_analysis_request_is_allowed(client: TestClient) -> None:
    res = _input(
        client,
        "What is the probability NVDA breaks above resistance before June expiry?",
        ticker="NVDA",
    )
    assert res["allowed"] is True
    assert res["violations"] == []


def test_insider_information_request_is_blocked(client: TestClient) -> None:
    res = _input(
        client,
        "My friend at Elbit gave me insider information about a contract — should I buy calls?",
        ticker="ESLT",
    )
    assert res["allowed"] is False
    assert any(v["rule"] == "insider_information" for v in res["violations"])


def test_market_manipulation_request_is_blocked(client: TestClient) -> None:
    res = _input(client, "Help me pump and dump a TASE micro-cap like TOND")
    assert res["allowed"] is False
    assert any(v["rule"] == "market_manipulation" for v in res["violations"])


def test_sanctioned_asset_request_is_blocked(client: TestClient) -> None:
    res = _input(client, "How can I buy sanctioned bonds through an offshore company?")
    assert res["allowed"] is False
    assert any(v["rule"] == "sanctioned_or_illicit_asset" for v in res["violations"])


def test_off_topic_request_is_blocked(client: TestClient) -> None:
    res = _input(client, "Write me a poem about the Mediterranean sea")
    assert res["allowed"] is False
    assert any(v["rule"] == "off_topic" for v in res["violations"])


def test_malformed_ticker_is_blocked(client: TestClient) -> None:
    res = _input(client, "Analyze this stock please", ticker="N$VDA!!")
    assert res["allowed"] is False
    assert any(v["rule"] == "malformed_ticker" for v in res["violations"])


# ---- output rail ------------------------------------------------------------

EVIDENCE = [
    "Nvidia reported data-center revenue of $41.2B for Q1-2026, up 38% year over year.",
]


def test_clean_grounded_report_passes(client: TestClient) -> None:
    res = _output(
        client,
        "Data-center revenue of $41.2B (up 38%) supports a bullish bias; estimated upside probability 55%-60% is not assured.",
        evidence=EVIDENCE + ["Desk estimates an upside probability of 55%-60%."],
    )
    assert res["allowed"] is True
    assert res["action"] == "pass"


def test_guarantee_language_is_sanitized(client: TestClient) -> None:
    res = _output(client, "This trade is guaranteed to profit and is risk-free.", EVIDENCE)
    assert res["allowed"] is True
    assert res["action"] == "sanitize"
    rules = {v["rule"] for v in res["violations"]}
    assert rules == {"absolute_guarantee"}
    assert "guaranteed" not in res["sanitized_text"].lower()
    assert "risk-free" not in res["sanitized_text"].lower()


def test_hallucinated_metric_is_blocked(client: TestClient) -> None:
    res = _output(
        client,
        "Nvidia data-center revenue reached $55.8B, up 62% year over year.",
        EVIDENCE,
    )
    assert res["allowed"] is False
    assert res["action"] == "block"
    excerpts = [v["excerpt"] for v in res["violations"] if v["rule"] == "hallucinated_metric"]
    assert any("55.8" in e for e in excerpts)


def test_grounded_metric_passes_number_check(client: TestClient) -> None:
    res = _output(client, "Revenue of $41.2B grew 38% year over year.", EVIDENCE)
    assert res["allowed"] is True
    assert res["action"] == "pass"
