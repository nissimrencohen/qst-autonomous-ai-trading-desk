"""Agentic Engine — FastAPI entrypoint.

CrewAI multi-agent team (Technical Analyst, Fundamental Analyst, Risk Manager) synthesizing RAG and Vision outputs into a structured JSON probability report.
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.logging_conf import configure_logging

configure_logging()

app = FastAPI(title="Agentic Engine", version=__version__, description="""CrewAI multi-agent team (Technical Analyst, Fundamental Analyst, Risk Manager) synthesizing RAG and Vision outputs into a structured JSON probability report.""")

_STARTED_AT = time.monotonic()


def readiness_checks() -> dict[str, bool]:
    """Dependency probes for /ready. Real checks land with the core logic step."""
    return {"config": True}


@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness — the process is up and serving requests."""
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": __version__,
        "uptime_s": round(time.monotonic() - _STARTED_AT, 1),
    }


@app.get("/ready", tags=["ops"])
def ready() -> JSONResponse:
    """Readiness — every service dependency is reachable."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
