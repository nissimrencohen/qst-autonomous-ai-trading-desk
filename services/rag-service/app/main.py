"""RAG Service — FastAPI entrypoint.

Retrieves historical financial reports and news from ChromaDB (HuggingFace
embeddings) and summarizes context via AWS Bedrock or local Llama.cpp/Ollama.
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
from app.logging_conf import configure_logging
from app.store import build_store
from app.summarizer import build_summarizer

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = build_store()
    app.state.summarizer = build_summarizer()
    log.info(
        "store=%s summarizer=%s documents=%d",
        app.state.store.name, app.state.summarizer.name, app.state.store.count(),
    )
    yield


app = FastAPI(
    title="RAG Service",
    version=__version__,
    description="Historical financial reports/news retrieval + LLM summarization.",
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
    store = getattr(app.state, "store", None)
    summarizer = getattr(app.state, "summarizer", None)
    return {
        "config": True,
        "store": bool(store and store.ping()),
        "summarizer": summarizer is not None,
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
    """Readiness — vector store and summarizer are initialized and reachable."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
