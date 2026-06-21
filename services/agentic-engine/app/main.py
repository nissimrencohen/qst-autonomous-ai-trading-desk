"""Agentic Engine — FastAPI entrypoint.

CrewAI multi-agent team (Technical Analyst, Fundamental Analyst, Risk
Manager) synthesizing RAG and Vision outputs into a structured JSON
probability report.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import litellm

from app import __version__
from app.api import router
from app.auth import auth_router
from app.config import settings
from app.engine import build_engine
from app.memory import build_memory
from app.logging_conf import configure_logging
from app.langfuse_tracing import configure_langfuse_litellm
from app.otel import configure_otel
from app.briefing_store import BriefingStore
from app.daily_briefing import start_briefing_scheduler, stop_briefing_scheduler
from app.runs import build_run_store
from app.social_pipeline import start_pipeline, stop_pipeline
from app.ingestion_engine import start_ingestion_engine, stop_ingestion_engine
from app.report_store import ReportStore
from app.synthesis_loop import start_synthesis_loop, stop_synthesis_loop
from app.chat import chat_router
from app.chat_store import ChatStore, InsightExtractor
from app.users import UserStore

# Silently drop provider-specific params that individual backends don't support.
litellm.drop_params = True

# Pre-populate litellm's global key registry so keys are available even when
# CrewAI's LLM wrapper omits explicit api_key forwarding to litellm.completion.
def _configure_litellm_keys() -> None:
    if k := settings.groq_api_key.get_secret_value():
        litellm.groq_key = k
    if k := settings.openai_api_key.get_secret_value():
        litellm.openai_key = k

_configure_litellm_keys()

configure_langfuse_litellm()
configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = build_engine()
    app.state.runs = build_run_store()
    app.state.memory = build_memory()
    app.state.bg_tasks = set()

    # DB-backed RBAC — users table + seed default admin/standard users on first
    # boot (only when the table is empty). Powers POST /auth/token + role gating.
    app.state.users = UserStore(settings.users_db_path)
    app.state.users.seed_defaults(
        admin_password=settings.auth_admin_password.get_secret_value() or "admin",
        user_password=settings.auth_user_password.get_secret_value() or "user",
    )

    # V2.0: Chat assistant — raw transcript store + insight extractor
    chat_db_path = str(settings.memory_db_path).replace("agent_memory.db", "chat_history.db")
    app.state.chat_store = ChatStore(chat_db_path)
    # Wire ChromaDB client if RAG store is chroma; else InsightExtractor degrades gracefully
    _chroma_client = None
    try:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path="./data/chroma")
    except Exception:  # noqa: BLE001
        log.warning("ChromaDB unavailable — insight extraction will be skipped")
    app.state.insight_extractor = InsightExtractor(_chroma_client)

    # v1.4: daily briefing store + scheduler
    app.state.briefing_store = BriefingStore(settings.briefing_db_path)
    app.state.briefing_task = None
    if settings.briefing_scheduler_enabled:
        app.state.briefing_task = start_briefing_scheduler(
            app.state.engine, app.state.runs, app.state.briefing_store
        )

    # Social media pipeline — runs as a persistent background asyncio Task.
    # The Task reference is kept on app.state so it isn't garbage-collected.
    app.state.social_task = None
    if settings.social_pipeline_enabled:
        app.state.social_task = start_pipeline(settings)

    # 1-minute continuous ingestion engine (Step 2d) — fetches news, TA,
    # macro, competitors for all 10 watchlist tickers on a 60 s cadence.
    app.state.ingestion_task = None
    if settings.ingestion_enabled:
        app.state.ingestion_task = start_ingestion_engine(settings)

    # Continuous synthesis loop (Step 2e) — sequential round-robin over the 10
    # tickers, reading EXCLUSIVELY from the ingestion cache. Opt-in (off by
    # default) to protect LLM budgets; enable via AGENTIC_SYNTHESIS_LOOP_ENABLED.
    app.state.report_store = ReportStore(settings.synthesis_report_db_path)
    app.state.synthesis_task = None
    if settings.synthesis_loop_enabled:
        app.state.synthesis_task = start_synthesis_loop(
            settings, app.state.runs, app.state.report_store
        )

    log.info(
        "engine=%s backend=%s memory=%s run_store=%s social=%s ingestion=%s synthesis_loop=%s auth=%s briefing=%s chat_store=%s",
        app.state.engine.name,
        settings.engine_backend,
        settings.memory_backend,
        settings.run_store_backend,
        "enabled" if settings.social_pipeline_enabled else "disabled",
        "enabled" if settings.ingestion_enabled else "disabled",
        "enabled" if settings.synthesis_loop_enabled else "disabled",
        "enabled" if settings.auth_enabled else "disabled",
        "scheduled" if settings.briefing_scheduler_enabled else "manual",
        chat_db_path,
    )
    yield

    if app.state.synthesis_task:
        stop_synthesis_loop(app.state.synthesis_task)
        try:
            await asyncio.wait_for(app.state.synthesis_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if app.state.briefing_task:
        stop_briefing_scheduler(app.state.briefing_task)
        try:
            await asyncio.wait_for(app.state.briefing_task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if app.state.social_task:
        stop_pipeline(app.state.social_task)
        try:
            await asyncio.wait_for(app.state.social_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    if app.state.ingestion_task:
        stop_ingestion_engine(app.state.ingestion_task)
        try:
            await asyncio.wait_for(app.state.ingestion_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


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
app.include_router(auth_router)   # v1.4: POST /auth/token
app.include_router(chat_router)   # V2.0: POST /chat, GET /chat/history
configure_otel(app, "agentic-engine")

_STARTED_AT = time.monotonic()


def readiness_checks() -> dict[str, bool]:
    return {
        "config": True,
        "engine": getattr(app.state, "engine", None) is not None,
        "run_store": getattr(app.state, "runs", None) is not None,
    }


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness — the process is up and serving requests."""
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": __version__,
        "uptime_s": round(time.monotonic() - _STARTED_AT, 1),
    }


@app.get("/ready", tags=["ops"])
async def ready() -> JSONResponse:
    """Readiness — synthesis engine and run store are initialized."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
