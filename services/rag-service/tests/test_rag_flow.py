"""Ingest -> query flow tests (memory store + extractive summarizer)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

DOCS = [
    {
        "ticker": "NVDA",
        "source": "Q1-2026 earnings call",
        "title": "Nvidia Q1-2026 results",
        "text": (
            "Nvidia reported data-center revenue of $41.2B for Q1-2026, up 38% "
            "year over year. Management raised full-year guidance citing sustained "
            "demand for Blackwell-generation accelerators. Gross margin held at 74%."
        ),
        "published_at": "2026-05-21",
    },
    {
        "ticker": "NVDA",
        "source": "options desk note",
        "title": "NVDA options flow ahead of earnings",
        "text": (
            "Open interest in NVDA June call options at the 1300 strike rose 4x in "
            "two weeks. Implied volatility for the front month sits at 52%, the "
            "highest since the January 2026 selloff."
        ),
        "published_at": "2026-05-12",
    },
    {
        "ticker": "ESLT",
        "source": "press release",
        "title": "Elbit Systems wins European contract",
        "text": (
            "Elbit Systems announced a $760M contract to supply precision-guided "
            "munitions to a European NATO member over five years. The backlog now "
            "stands at a record $22.6B, with Europe contributing 41% of new orders."
        ),
        "published_at": "2026-04-03",
    },
]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_ingest_returns_counts(client: TestClient) -> None:
    res = client.post("/ingest", json={"documents": DOCS})
    assert res.status_code == 200
    body = res.json()
    assert body["ingested"] == 3
    assert body["store_backend"] == "memory"
    assert body["total_documents"] >= 3


def test_query_retrieves_relevant_docs_and_summarizes(client: TestClient) -> None:
    client.post("/ingest", json={"documents": DOCS})
    res = client.post(
        "/query",
        json={"ticker": "NVDA", "question": "What happened to data-center revenue?", "k": 2},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ticker"] == "NVDA"
    assert 1 <= len(body["retrieved"]) <= 2
    # ticker filter must exclude the ESLT doc
    assert all(d["ticker"] == "NVDA" for d in body["retrieved"])
    # extractive summary quotes the source fact and attributes it
    assert "41.2" in body["summary"]
    assert "[source:" in body["summary"]
    assert body["summarizer_backend"] == "extractive"


def test_query_unknown_ticker_returns_empty_with_null_summary(client: TestClient) -> None:
    client.post("/ingest", json={"documents": DOCS})
    res = client.post(
        "/query", json={"ticker": "CUE", "question": "Any biotech catalysts?"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["retrieved"] == []
    assert body["summary"] is None


def test_ingest_rejects_empty_documents(client: TestClient) -> None:
    res = client.post("/ingest", json={"documents": []})
    assert res.status_code == 422
