"""Two-tier social media noise filter.

Tier 1 (heuristic/regex) — zero-cost Python:
  Scans raw text for tracked ticker symbols ($AAPL, word-boundary NVDA) or
  financial keywords. Drops anything that doesn't match — this is the cost gate.

Tier 2 (LLM sentiment) — lightweight litellm batch call:
  Passes surviving posts in batches of up to BATCH_SIZE to the cheapest
  available provider (Groq → OpenAI fallback via provider_chain()).
  The model classifies sentiment, extracts the core thesis, and flags
  memes/sarcasm for discard. Returns structured SocialSignal objects.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger(__name__)

# ── shared types ──────────────────────────────────────────────────────────────

@dataclass
class RawSocialPost:
    source: str            # "reddit" | "telegram"
    source_id: str         # unique ID for deduplication
    platform_url: str
    author: str
    text: str              # title + body / message text
    upvotes: int
    timestamp: datetime
    subreddit: str | None = None
    channel: str | None = None


@dataclass
class SocialSignal:
    source_id: str
    source: str
    primary_ticker: str
    all_tickers: list[str] = field(default_factory=list)
    sentiment: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = 0.5
    thesis: str = ""
    platform_url: str = ""
    author: str = ""
    upvotes: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    subreddit: str | None = None
    channel: str | None = None


# ── tier-1: heuristic / regex ─────────────────────────────────────────────────

# Project tickers (from domain rules) + broad market coverage
TRACKED_TICKERS: frozenset[str] = frozenset({
    # project-domain tickers
    "NVDA", "ESLT", "NXSN", "TOND", "CUE",
    # major US equities & ETFs
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AMD", "INTC",
    "NFLX", "BABA", "ORCL", "CRM", "ADBE", "QCOM", "AVGO", "MU", "SMCI",
    "SPY", "QQQ", "IWM", "DIA", "ARKK", "SOXS", "SOXL", "UVXY", "VXX", "SVXY",
    "SPCX",
    # popular options-chain / meme names
    "GME", "AMC", "BBBY", "MSTR", "COIN",
    # common indices
    "SPX", "NDX", "VIX",
})

# Dollar-sign ticker pattern: $AAPL, $tsla (case-insensitive)
_DOLLAR_TICKER = re.compile(r"\$([A-Za-z]{1,5})\b")

# Word-boundary ticker pattern — only for TRACKED_TICKERS to avoid false positives
# Built lazily on first call so TRACKED_TICKERS can be extended.
_WORD_TICKER_PATTERN: re.Pattern | None = None


def _word_ticker_re() -> re.Pattern:
    global _WORD_TICKER_PATTERN
    if _WORD_TICKER_PATTERN is None:
        alts = "|".join(sorted(TRACKED_TICKERS, key=len, reverse=True))
        _WORD_TICKER_PATTERN = re.compile(rf"\b({alts})\b")
    return _WORD_TICKER_PATTERN


FINANCIAL_KEYWORDS: frozenset[str] = frozenset({
    "earnings", "revenue", "eps", "pe ratio", "valuation", "buyback",
    "dividend", "short squeeze", "calls", "puts", "options", "iv crush",
    "gamma squeeze", "yolo", "moon", "bear", "bull", "rally", "correction",
    "support", "resistance", "breakout", "breakdown", "oversold", "overbought",
    "ipo", "spac", "merger", "acquisition", "sec filing", "10-k", "10-q",
    "hedge fund", "institutional", "dark pool", "market cap", "float",
    "short interest", "vix", "inflation", "fed", "fomc", "rate hike",
    "crypto", "bitcoin", "eth", "defi", "nft",
})


def tier1_filter(post: RawSocialPost) -> list[str]:
    """Return list of matched tickers if the post passes the heuristic gate.

    Returns an empty list if the post should be dropped (no financial signal).
    Fast path: compiled regex, frozenset lookups — no I/O, no LLM cost.
    """
    text_lower = post.text.lower()

    # 1. Dollar-sign tickers ($NVDA, $tsla)
    dollar_matches = {m.upper() for m in _DOLLAR_TICKER.findall(post.text)}
    tracked_dollar = dollar_matches & TRACKED_TICKERS

    # 2. Word-boundary tracked tickers
    word_matches = {m.upper() for m in _word_ticker_re().findall(post.text)}

    matched_tickers = list(tracked_dollar | word_matches)

    # 3. If no ticker found, require at least one financial keyword
    if not matched_tickers:
        if any(kw in text_lower for kw in FINANCIAL_KEYWORDS):
            # pass on keywords but no specific ticker — use "MARKET" as placeholder
            return ["MARKET"]
        return []  # drop

    return matched_tickers


# ── tier-2: LLM sentiment ────────────────────────────────────────────────────

BATCH_SIZE = 10  # posts per litellm call

_SYSTEM_PROMPT = """\
You are a financial sentiment analyst. You receive social media posts (Reddit/Telegram)
and must classify each one for investment relevance.

For each post, output a JSON object with these exact keys:
  source_id   : string  — copy exactly from input
  primary_ticker : string — the single most-discussed ticker (uppercase), or "MARKET"
  all_tickers : array of strings — ALL tickers mentioned
  sentiment   : "bullish" | "bearish" | "neutral"
  confidence  : float 0.0–1.0 — how clear the signal is
  thesis      : string — 1-2 sentences capturing the core investment thesis
  is_relevant : boolean — false for pure memes, off-topic rants, or pure sarcasm with no thesis

Respond ONLY with a JSON array containing one object per input post, in the same order.
No markdown, no explanation — raw JSON only.\
"""


def _build_user_prompt(batch: list[tuple[RawSocialPost, list[str]]]) -> str:
    items = []
    for post, tickers in batch:
        items.append({
            "source_id": post.source_id,
            "source": post.source,
            "subreddit_or_channel": post.subreddit or post.channel or "",
            "upvotes": post.upvotes,
            "matched_tickers": tickers,
            "text": post.text[:600],  # hard cap — avoid token bloat
        })
    return json.dumps(items, ensure_ascii=False)


async def tier2_analyze(
    batch: list[tuple[RawSocialPost, list[str]]],
) -> list[SocialSignal]:
    """Pass a batch of tier-1 survivors through the LLM sentiment filter.

    Uses litellm.acompletion() directly (not CrewAI) for speed and cost
    efficiency. Iterates provider_chain() until one succeeds.
    """
    if not batch:
        return []

    import litellm
    from app.llm_router import provider_chain

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(batch)},
    ]

    raw_text: str | None = None
    for model, kwargs in provider_chain():
        call_kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 1500,
        }
        if "api_key" in kwargs:
            call_kwargs["api_key"] = kwargs["api_key"]
        if "api_base" in kwargs:
            call_kwargs["api_base"] = kwargs["api_base"]
        if "extra_headers" in kwargs:
            call_kwargs["extra_headers"] = kwargs["extra_headers"]

        try:
            resp = await litellm.acompletion(**call_kwargs)
            raw_text = resp.choices[0].message.content or ""
            log.debug("tier2 LLM succeeded via model=%s batch_size=%d", model, len(batch))
            break
        except Exception as exc:
            log.warning("tier2 LLM failed model=%s: %s", model, exc)

    if not raw_text:
        log.error("tier2: all providers failed — batch of %d dropped", len(batch))
        return []

    # Parse JSON — strip accidental markdown fences
    try:
        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
        parsed: list[dict] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.warning("tier2: JSON parse failed (%s) — batch dropped", exc)
        return []

    post_map = {post.source_id: post for post, _ in batch}
    signals: list[SocialSignal] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("is_relevant", True):
            log.debug("tier2: dropped irrelevant post source_id=%s", item.get("source_id"))
            continue
        sid = item.get("source_id", "")
        post = post_map.get(sid)
        if post is None:
            continue
        signals.append(SocialSignal(
            source_id=sid,
            source=post.source,
            primary_ticker=str(item.get("primary_ticker", "MARKET")).upper(),
            all_tickers=[t.upper() for t in item.get("all_tickers", [])],
            sentiment=item.get("sentiment", "neutral"),
            confidence=float(item.get("confidence", 0.5)),
            thesis=str(item.get("thesis", ""))[:400],
            platform_url=post.platform_url,
            author=post.author,
            upvotes=post.upvotes,
            timestamp=post.timestamp,
            processed_at=datetime.now(timezone.utc),
            subreddit=post.subreddit,
            channel=post.channel,
        ))

    log.info(
        "tier2: batch=%d → signals=%d (dropped=%d)",
        len(batch), len(signals), len(batch) - len(signals),
    )
    return signals
