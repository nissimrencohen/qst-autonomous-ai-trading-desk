"""Guardrails Service — FastAPI entrypoint.

NeMo Guardrails validation: blocks off-topic/illegal-asset requests on
input; blocks absolute financial guarantees and hallucinated metrics on
output. Deterministic rules run before any LLM rail.
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
from app.engine import build_backend
from app.logging_conf import configure_logging
from app.otel import configure_otel

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.guard = build_backend()
    log.info("guardrails backend=%s", app.state.guard.name)
    yield


app = FastAPI(
    title="Guardrails Service",
    version=__version__,
    description="Input/output validation rails for the trading desk.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
configure_otel(app, "guardrails-service")

_STARTED_AT = time.monotonic()


def readiness_checks() -> dict[str, bool]:
    return {"config": True, "guard": getattr(app.state, "guard", None) is not None}


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
    """Readiness — the guardrails backend is loaded."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
