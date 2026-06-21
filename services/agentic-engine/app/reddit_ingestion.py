"""Async Reddit ingestion via asyncpraw.

Polls the configured subreddits for hot posts (+ top comments) and yields
RawSocialPost objects. Fully async — never blocks the FastAPI event loop.

Credentials (env, AGENTIC_ prefix via config.py):
  AGENTIC_REDDIT_CLIENT_ID      — from https://www.reddit.com/prefs/apps
  AGENTIC_REDDIT_CLIENT_SECRET  — script-app secret
  AGENTIC_REDDIT_USER_AGENT     — descriptive UA string (required by Reddit ToS)

If any credential is missing, the fetcher logs a warning and yields nothing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from app.social_signal_processor import RawSocialPost

log = logging.getLogger(__name__)

_INTER_SUBREDDIT_SLEEP_S = 2.0   # pause between subreddits to respect rate limits
_INTER_POST_SLEEP_S = 0.05       # tiny pause between posts within a subreddit


async def fetch_reddit_posts(
    client_id: str,
    client_secret: str,
    user_agent: str,
    subreddits: list[str],
    post_limit: int = 25,
    comment_limit: int = 5,
) -> AsyncIterator[RawSocialPost]:
    """Yield hot posts and top comments from the given subreddits.

    Args:
        client_id:      Reddit OAuth client ID.
        client_secret:  Reddit OAuth client secret.
        user_agent:     Descriptive user-agent string (Reddit policy requires this).
        subreddits:     List of subreddit names (without "r/").
        post_limit:     How many hot posts to fetch per subreddit.
        comment_limit:  Top-N comments to fetch per post (0 to skip comments).
    """
    if not client_id or not client_secret:
        log.warning("Reddit credentials missing (AGENTIC_REDDIT_CLIENT_ID / SECRET) — skipping")
        return

    try:
        import asyncpraw
        import asyncpraw.exceptions
    except ImportError:
        log.error("asyncpraw not installed — pip install asyncpraw")
        return

    async with asyncpraw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        # Explicit read-only mode: no write actions possible even if credentials allow it.
    ) as reddit:
        reddit.read_only = True

        for sub_name in subreddits:
            try:
                subreddit = await reddit.subreddit(sub_name)
                async for submission in subreddit.hot(limit=post_limit):
                    text = f"{submission.title}\n{submission.selftext}".strip()
                    yield RawSocialPost(
                        source="reddit",
                        source_id=f"reddit_{submission.id}",
                        platform_url=f"https://reddit.com{submission.permalink}",
                        author=str(submission.author or "deleted"),
                        text=text,
                        upvotes=submission.score,
                        timestamp=datetime.fromtimestamp(
                            submission.created_utc, tz=timezone.utc
                        ),
                        subreddit=sub_name,
                    )

                    if comment_limit > 0:
                        await _yield_top_comments(submission, sub_name, comment_limit)

                    await asyncio.sleep(_INTER_POST_SLEEP_S)

                log.debug("Reddit: fetched r/%s (limit=%d)", sub_name, post_limit)
                await asyncio.sleep(_INTER_SUBREDDIT_SLEEP_S)

            except asyncpraw.exceptions.AsyncPRAWException as exc:
                log.warning("Reddit API error for r/%s: %s", sub_name, exc)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly
            except Exception as exc:
                log.warning("Unexpected error for r/%s: %s", sub_name, exc)


async def _yield_top_comments(
    submission,
    sub_name: str,
    limit: int,
) -> AsyncIterator[RawSocialPost]:
    """Yield the top N comments of a submission.

    Separate helper so exceptions in comment fetching never abort the outer post loop.
    Note: this is a regular async generator called with `async for` from the parent.
    """
    try:
        await submission.load()
        comments = submission.comments[:limit]
        for comment in comments:
            # MoreComments objects don't have .body — skip them
            if not hasattr(comment, "body"):
                continue
            yield RawSocialPost(
                source="reddit",
                source_id=f"reddit_comment_{comment.id}",
                platform_url=f"https://reddit.com{submission.permalink}",
                author=str(comment.author or "deleted"),
                text=comment.body,
                upvotes=comment.score,
                timestamp=datetime.fromtimestamp(
                    comment.created_utc, tz=timezone.utc
                ),
                subreddit=sub_name,
            )
    except Exception as exc:
        log.debug("comment fetch failed for %s: %s", submission.id, exc)
