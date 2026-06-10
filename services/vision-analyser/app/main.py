"""Vision Analyser — FastAPI entrypoint.

Scores technical chart screenshots with PyTorch (ResNet-50/EfficientNet):
support, resistance, breakouts -> bullish/bearish condition score.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import router
from app.config import settings
from app.logging_conf import configure_logging

configure_logging()
log = logging.getLogger(__name__)

app = FastAPI(
    title="Vision Analyser",
    version=__version__,
    description="Technical chart screenshot -> bullish/bearish condition score.",
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
    from app.inference import get_analyser

    try:
        get_analyser()
        analyser_ok = True
    except Exception:
        log.exception("analyser failed to load")
        analyser_ok = False
    return {"config": True, "analyser": analyser_ok}


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
    """Readiness — model backend is loaded and usable."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
