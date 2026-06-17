"""Agentic Engine — FastAPI entrypoint.

CrewAI multi-agent team (Technical Analyst, Fundamental Analyst, Risk
Manager) synthesizing RAG and Vision outputs into a structured JSON
probability report.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import router
from app.config import settings
from app.engine import build_engine
from app.logging_conf import configure_logging
from app.runs import RunStore

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = build_engine()
    app.state.runs = RunStore()
    log.info(
        "engine=%s backend=%s memory=%s",
        app.state.engine.name,
        settings.engine_backend,
        settings.memory_backend,
    )
    yield


app = FastAPI(
    title="Agentic Engine",
    version=__version__,
    description="Multi-agent synthesis of RAG + Vision outputs into probability reports.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

_STARTED_AT = time.monotonic()


def readiness_checks() -> dict[str, bool]:
    return {
        "config": True,
        "engine": getattr(app.state, "engine", None) is not None,
        "run_store": getattr(app.state, "runs", None) is not None,
    }


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
    """Readiness — synthesis engine and run store are initialized."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
