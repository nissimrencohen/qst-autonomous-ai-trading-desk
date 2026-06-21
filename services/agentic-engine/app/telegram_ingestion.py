"""Async Telegram ingestion via Pyrogram.

Connects to public financial channels in read-only mode and yields
RawSocialPost objects. Implements mandatory jitter + FloodWait handling
to prevent account bans from aggressive polling.

Credentials (env, AGENTIC_ prefix via config.py):
  User-API mode  (preferred — can read any public channel):
    AGENTIC_TELEGRAM_API_ID    — integer ID from https://my.telegram.org
    AGENTIC_TELEGRAM_API_HASH  — hash from https://my.telegram.org

  Bot-API mode  (bot must be a channel member to read it):
    AGENTIC_TELEGRAM_BOT_TOKEN — BotFather token

  Channel list:
    AGENTIC_TELEGRAM_CHANNELS  — comma-separated public channel usernames
                                  (e.g. "investing_news,wsb_alerts")

If no credentials are configured the fetcher logs a warning and yields nothing.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import AsyncIterator

from app.social_signal_processor import RawSocialPost

log = logging.getLogger(__name__)

_MIN_JITTER_S = 3.0   # minimum sleep between channel requests
_MAX_JITTER_S = 9.0   # maximum sleep — randomized per channel
_FLOOD_EXTRA_JITTER_S = 5.0  # extra randomness added on top of FloodWait


async def fetch_telegram_messages(
    api_id: int | None,
    api_hash: str | None,
    bot_token: str | None,
    channels: list[str],
    history_limit: int = 20,
) -> AsyncIterator[RawSocialPost]:
    """Yield recent messages from the given Telegram channel list.

    Prefers User-API (api_id + api_hash) over Bot-API because bots can only
    read channels they are members of. Both modes are read-only.

    Args:
        api_id:        Telegram app ID (User-API). None for bot-only mode.
        api_hash:      Telegram app hash (User-API). None for bot-only mode.
        bot_token:     Bot token (Bot-API). None for user-only mode.
        channels:      Public channel usernames (without '@').
        history_limit: Max messages to pull per channel per cycle.
    """
    if not channels:
        log.warning("No Telegram channels configured (AGENTIC_TELEGRAM_CHANNELS)")
        return

    if not ((api_id and api_hash) or bot_token):
        log.warning(
            "Telegram credentials not set "
            "(AGENTIC_TELEGRAM_API_ID+HASH or AGENTIC_TELEGRAM_BOT_TOKEN) — skipping"
        )
        return

    try:
        from pyrogram import Client
    except ImportError:
        log.error("pyrogram not installed — pip install pyrogram")
        return

    if bot_token and not (api_id and api_hash):
        # Bot-API mode: requires api_id/api_hash even for bots in newer Pyrogram;
        # use environment placeholders if provided keys are missing.
        log.warning(
            "Bot token found but no API id/hash — "
            "Pyrogram requires these for Bot-API too. Set AGENTIC_TELEGRAM_API_ID/HASH."
        )
        return

    # no_updates=True → suppress server-push updates; we only pull history.
    client_kwargs: dict = {
        "name": "trading_desk",
        "api_id": api_id,
        "api_hash": api_hash,
        "no_updates": True,
        "in_memory": True,  # session stored in-memory — no session file on disk
    }
    if bot_token:
        client_kwargs["bot_token"] = bot_token

    async with Client(**client_kwargs) as app:
        for channel in channels:
            jitter = random.uniform(_MIN_JITTER_S, _MAX_JITTER_S)
            await asyncio.sleep(jitter)

            try:
                async for msg in app.get_chat_history(channel, limit=history_limit):
                    text = msg.text or msg.caption or ""
                    if not text:
                        continue
                    from_user = msg.from_user
                    author = (
                        from_user.username or from_user.first_name
                        if from_user
                        else channel
                    )
                    msg_date = msg.date
                    ts = (
                        datetime.fromtimestamp(msg_date.timestamp(), tz=timezone.utc)
                        if hasattr(msg_date, "timestamp")
                        else datetime.now(timezone.utc)
                    )
                    yield RawSocialPost(
                        source="telegram",
                        source_id=f"tg_{channel}_{msg.id}",
                        platform_url=f"https://t.me/{channel}/{msg.id}",
                        author=str(author),
                        text=text,
                        upvotes=0,  # Telegram channels don't expose public reaction counts
                        timestamp=ts,
                        channel=channel,
                    )

                log.debug("Telegram: fetched %d messages from @%s", history_limit, channel)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                exc_name = type(exc).__name__
                if "FloodWait" in exc_name:
                    # Pyrogram raises FloodWait with .value = required wait seconds
                    wait_s = getattr(exc, "value", 30)
                    sleep_s = wait_s + random.uniform(1.0, _FLOOD_EXTRA_JITTER_S)
                    log.warning(
                        "Telegram FloodWait %ds for @%s — sleeping %.1fs",
                        wait_s, channel, sleep_s,
                    )
                    await asyncio.sleep(sleep_s)
                elif "ChatInvalid" in exc_name or "UsernameNotOccupied" in exc_name:
                    log.warning("Telegram channel @%s is invalid or private — skipping", channel)
                else:
                    log.warning("Telegram fetch error for @%s: %s", channel, exc)
