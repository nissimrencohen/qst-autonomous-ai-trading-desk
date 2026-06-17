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

import litellm

from app import __version__
from app.api import router
from app.config import settings
from app.engine import build_engine
from app.logging_conf import configure_logging
from app.runs import RunStore

# CrewAI >=0.80 injects cache_breakpoint into system messages for Anthropic
# prompt caching. Non-Anthropic providers (Groq, OpenAI, GitHub) reject it.
litellm.drop_params = True

# Pre-populate litellm's global key registry so keys are available even when
# CrewAI's LLM wrapper omits explicit api_key forwarding to litellm.completion.
def _configure_litellm_keys() -> None:
    if k := settings.groq_api_key.get_secret_value():
        litellm.groq_key = k
    if k := settings.openai_api_key.get_secret_value():
        litellm.openai_key = k

_configure_litellm_keys()

# crewai/llm.py (old LLM class) does not strip cache_breakpoint from messages
# before forwarding to litellm — only the newer crewai/llms/base_llm.py does.
# Patch LLM.call() here so every provider rejects Anthropic-specific markers.
def _patch_crewai_llm_strip_cache_breakpoint() -> None:
    try:
        from crewai import LLM as _CrewLLM
        from crewai.llms.cache import CACHE_BREAKPOINT_KEY
    except ImportError:
        return

    _orig = _CrewLLM.call

    def _call_patched(self, messages, *args, **kwargs):
        if isinstance(messages, list):
            messages = [
                {k: v for k, v in m.items() if k != CACHE_BREAKPOINT_KEY}
                if isinstance(m, dict) else m
                for m in messages
            ]
        return _orig(self, messages, *args, **kwargs)

    _CrewLLM.call = _call_patched

_patch_crewai_llm_strip_cache_breakpoint()

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
