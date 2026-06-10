"""Smoke tests for the ops endpoints."""
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "agentic-engine"


def test_ready_reports_all_checks_passing() -> None:
    # context manager runs the lifespan, which builds engine + run store
    with TestClient(app) as client:
        res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True
    assert body["checks"] and all(body["checks"].values())
