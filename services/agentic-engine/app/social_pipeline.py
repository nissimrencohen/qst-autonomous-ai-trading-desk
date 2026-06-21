"""Social media ingestion pipeline coordinator.

Runs as a long-lived background asyncio Task inside the agentic-engine process.
Every POLL_INTERVAL seconds it:
  1. Fetches raw posts from Reddit + Telegram ingestion modules
  2. Deduplicates via a TTL cache (24 h window, keyed by source_id)
  3. Passes tier-1 survivors through the two-tier processor in BATCH_SIZE chunks
  4. Stores processed SocialSignals in a per-ticker in-memory cache (deque)
  5. Pushes signals to the RAG /ingest endpoint so agents can query them via vector search

Public synchronous API (called from orchestrator.py — no I/O):
  get_social_context(ticker: str) -> str
      Returns a formatted string of recent signals for a ticker, or "" if none.

Lifecycle (called from main.py lifespan):
  start_pipeline(cfg: Settings) -> asyncio.Task
  stop_pipeline(task: asyncio.Task) -> None
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import requests

from app.social_signal_processor import (
    BATCH_SIZE,
    RawSocialPost,
    SocialSignal,
    tier1_filter,
    tier2_analyze,
)

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

# ── in-memory state ───────────────────────────────────────────────────────────

# Dedup cache: source_id → ingested_at.  Pruned every cycle (24 h TTL).
_seen: dict[str, datetime] = {}
_DEDUP_TTL = timedelta(hours=24)

# Per-ticker signal cache: ticker → deque of SocialSignal (newest first, cap 50).
_MAX_SIGNALS_PER_TICKER = 50
_signals: dict[str, deque[SocialSignal]] = defaultdict(
    lambda: deque(maxlen=_MAX_SIGNALS_PER_TICKER)
)

# Context window for the crew prompt: keep last N signals per ticker.
_CONTEXT_WINDOW = 5


# ── public API ────────────────────────────────────────────────────────────────

def get_social_context(ticker: str) -> str:
    """Return a formatted social-signal block for the given ticker.

    Called synchronously from orchestrator.py — reads from the in-memory
    cache only (no I/O, safe to call from any thread).
    """
    ticker = ticker.upper()
    signals = list(_signals.get(ticker, []))[:_CONTEXT_WINDOW]
    if not signals:
        return ""

    lines = [
        f"=== Community Social Signals: {ticker} "
        f"(last {len(signals)} processed signals) ===",
    ]
    for s in signals:
        source_label = (
            f"Reddit r/{s.subreddit}" if s.subreddit
            else f"Telegram @{s.channel}" if s.channel
            else s.source.capitalize()
        )
        conf_pct = int(s.confidence * 100)
        lines.append(
            f"[{source_label} | {s.sentiment.upper()} | conf={conf_pct}%"
            f" | {s.upvotes} upvotes] {s.thesis}"
        )
    return "\n".join(lines)


# ── lifecycle ─────────────────────────────────────────────────────────────────

def start_pipeline(cfg: "Settings") -> asyncio.Task:
    """Schedule the background pipeline loop and return the Task.

    Called from the FastAPI lifespan on startup. The Task holds a strong
    reference; store it in app.state to prevent GC.
    """
    task = asyncio.create_task(
        _pipeline_loop(cfg),
        name="social-pipeline",
    )
    log.info(
        "social-pipeline: started (interval=%ds reddit=%s telegram=%s)",
        cfg.social_poll_interval_s,
        bool(cfg.reddit_client_id),
        bool(cfg.telegram_api_id or cfg.telegram_bot_token),
    )
    return task


def stop_pipeline(task: asyncio.Task) -> None:
    """Cancel the background pipeline loop. Called from lifespan shutdown."""
    if task and not task.done():
        task.cancel()
        log.info("social-pipeline: stop requested")


# ── background loop ───────────────────────────────────────────────────────────

async def _pipeline_loop(cfg: "Settings") -> None:
    """Run one ingestion cycle then sleep; repeat forever until cancelled."""
    while True:
        try:
            await _run_cycle(cfg)
        except asyncio.CancelledError:
            log.info("social-pipeline: cancelled — shutting down")
            return
        except Exception as exc:
            log.exception("social-pipeline: unhandled error in cycle: %s", exc)
        await asyncio.sleep(cfg.social_poll_interval_s)


async def _run_cycle(cfg: "Settings") -> None:
    """One full ingestion cycle: fetch → dedup → filter → cache → RAG push."""
    from app.reddit_ingestion import fetch_reddit_posts
    from app.telegram_ingestion import fetch_telegram_messages

    raw_posts: list[RawSocialPost] = []

    # ── Reddit ────────────────────────────────────────────────────────────────
    if cfg.reddit_client_id:
        try:
            async for post in fetch_reddit_posts(
                client_id=cfg.reddit_client_id,
                client_secret=cfg.reddit_client_secret,
                user_agent=cfg.reddit_user_agent,
                subreddits=cfg.reddit_subreddits_list,
                post_limit=cfg.reddit_post_limit,
                comment_limit=cfg.reddit_comment_limit,
            ):
                raw_posts.append(post)
        except Exception as exc:
            log.warning("social-pipeline: Reddit fetch error: %s", exc)

    # ── Telegram ──────────────────────────────────────────────────────────────
    if cfg.telegram_api_id or cfg.telegram_bot_token:
        try:
            async for post in fetch_telegram_messages(
                api_id=cfg.telegram_api_id,
                api_hash=cfg.telegram_api_hash,
                bot_token=cfg.telegram_bot_token,
                channels=cfg.telegram_channels_list,
                history_limit=cfg.telegram_history_limit,
            ):
                raw_posts.append(post)
        except Exception as exc:
            log.warning("social-pipeline: Telegram fetch error: %s", exc)

    log.info("social-pipeline: raw posts fetched=%d", len(raw_posts))

    # ── Deduplication ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    _prune_dedup_cache(now)
    new_posts = [p for p in raw_posts if p.source_id not in _seen]
    for p in new_posts:
        _seen[p.source_id] = now

    log.info(
        "social-pipeline: after dedup new=%d (skipped=%d)",
        len(new_posts), len(raw_posts) - len(new_posts),
    )

    # ── Tier-1 filter ─────────────────────────────────────────────────────────
    tier1_survivors: list[tuple[RawSocialPost, list[str]]] = []
    for post in new_posts:
        matched = tier1_filter(post)
        if matched:
            tier1_survivors.append((post, matched))

    log.info(
        "social-pipeline: tier1 survivors=%d (dropped=%d)",
        len(tier1_survivors), len(new_posts) - len(tier1_survivors),
    )

    if not tier1_survivors:
        return

    # ── Tier-2 LLM analysis (batched) ────────────────────────────────────────
    all_signals: list[SocialSignal] = []
    for i in range(0, len(tier1_survivors), BATCH_SIZE):
        chunk = tier1_survivors[i : i + BATCH_SIZE]
        try:
            signals = await tier2_analyze(chunk)
            all_signals.extend(signals)
        except Exception as exc:
            log.warning("social-pipeline: tier2 batch error: %s", exc)

    log.info("social-pipeline: signals produced=%d", len(all_signals))

    # ── Update in-memory cache + push to RAG ─────────────────────────────────
    for signal in all_signals:
        _signals[signal.primary_ticker].appendleft(signal)
        # Also index under each mentioned ticker so cross-ticker queries work
        for tk in signal.all_tickers:
            if tk != signal.primary_ticker:
                _signals[tk].appendleft(signal)

        # Push to RAG in a thread (requests is sync; keep the event loop clean)
        asyncio.create_task(_push_to_rag(signal, cfg.rag_url))


async def _push_to_rag(signal: SocialSignal, rag_url: str) -> None:
    """POST the signal to the RAG /ingest endpoint as a financial document."""
    doc_text = (
        f"[{signal.source.upper()} | {signal.sentiment.upper()} | "
        f"conf={signal.confidence:.0%}] {signal.thesis}"
    )
    payload = {
        "documents": [{
            "id": signal.source_id,
            "title": f"Social Signal: {signal.primary_ticker} ({signal.sentiment})",
            "source": signal.platform_url,
            "published_at": signal.processed_at.isoformat(),
            "text": doc_text,
        }]
    }
    try:
        await asyncio.to_thread(
            requests.post,
            f"{rag_url}/ingest",
            json=payload,
            timeout=10,
        )
        log.debug("social-pipeline: pushed signal %s to RAG", signal.source_id)
    except Exception as exc:
        log.warning("social-pipeline: RAG push failed for %s: %s", signal.source_id, exc)


def _prune_dedup_cache(now: datetime) -> None:
    """Remove entries older than DEDUP_TTL from the seen-IDs cache."""
    cutoff = now - _DEDUP_TTL
    expired = [sid for sid, ts in _seen.items() if ts < cutoff]
    for sid in expired:
        del _seen[sid]
    if expired:
        log.debug("social-pipeline: pruned %d expired dedup entries", len(expired))
