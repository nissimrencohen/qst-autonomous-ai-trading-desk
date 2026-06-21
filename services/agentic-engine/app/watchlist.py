"""Canonical instrument watchlist — the single source of truth.

V2.0 mandates that the desk ONLY processes and accepts analyses for these
exact ten symbols. Every other module (gatekeeper, batch orchestrator, daily
briefing, request schemas, volatility-desk routing) imports from here so the
set can never drift out of sync again.

Strict enforcement (mission Req 1): a ticker that is not on this list is
rejected at the API boundary — see `assert_whitelisted` and the
`field_validator` on AnalyzeRequest / SynthesizeRequest in schemas.py.

Note on aliases: bare VIX proxies (VIX / ^VIX / VXX / UVXY) are intentionally
NOT accepted. The fear index is analysed for every ticker via the mandatory
macro/VIX context (Req 2) — it is not itself a tradeable watchlist member.
The two volatility ETFs that ARE on the list, VIXY (long-vol) and SVXY
(short-vol), are tradeable instruments in their own right.
"""
from __future__ import annotations

# ── the ten approved instruments (canonical uppercase) ───────────────────────
# Ordered: core equities → leveraged index ETFs → volatility ETFs.
WATCHLIST_ORDERED: tuple[str, ...] = (
    "SPCX",   # SpaceX (newly public)
    "MSFT",   # Microsoft
    "AAPL",   # Apple
    "NVDA",   # NVIDIA
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "UPRO",   # ProShares UltraPro S&P 500 (3x)
    "TQQQ",   # ProShares UltraPro QQQ (3x NASDAQ-100)
    "VIXY",   # ProShares VIX Short-Term Futures (long-vol)
    "SVXY",   # ProShares Short VIX Short-Term Futures (short-vol)
)

WATCHLIST: frozenset[str] = frozenset(WATCHLIST_ORDERED)

# Volatility-desk instruments — the Volatility Analyst LEADS on these and the
# thesis must hinge on VIX term-structure / mean-reversion dynamics.
VOL_TICKERS: frozenset[str] = frozenset({"VIXY", "SVXY"})

# Competitor / peer map (mission Req 3 — "analysing AMD must pull NVDA data").
# Maps each watchlist instrument → the peers whose data the desk pulls for
# relative read-through. Peers are intentionally allowed to be OFF the
# watchlist: we read their data for context, we do NOT analyse them as
# tradeable members. For the leveraged-index and volatility ETFs the "peers"
# are the closest related instruments (1x/2x/inverse, sibling VIX ETPs).
COMPETITOR_MAP: dict[str, tuple[str, ...]] = {
    "SPCX": ("RKLB", "ASTS", "LMT", "BA"),      # space / aerospace-defense
    "MSFT": ("GOOGL", "AMZN", "AAPL", "ORCL"),  # mega-cap tech / cloud
    "AAPL": ("MSFT", "GOOGL", "AMZN"),          # consumer tech mega-caps
    "NVDA": ("AMD", "AVGO", "INTC", "TSM"),     # AI accelerators / semis
    "GOOGL": ("MSFT", "META", "AMZN", "AAPL"),  # search / cloud / ads
    "AMZN": ("MSFT", "GOOGL", "WMT", "BABA"),   # cloud + e-commerce
    "UPRO": ("SPY", "SSO", "VOO", "SPXU"),      # S&P 500: 1x / 2x / inverse
    "TQQQ": ("QQQ", "QLD", "ONEQ", "SQQQ"),     # NASDAQ-100: 1x / 2x / inverse
    "VIXY": ("UVXY", "VXX", "VIXM", "SVXY"),    # VIX futures ETPs
    "SVXY": ("VIXY", "UVXY", "VXX", "SVIX"),    # short-vol / inverse VIX
}


def competitors_for(ticker: str) -> tuple[str, ...]:
    """Peer tickers for a watchlist instrument (empty tuple if unmapped)."""
    return COMPETITOR_MAP.get(normalize(ticker), ())


# Human-readable labels (frontend / briefing / logging convenience).
INSTRUMENT_LABELS: dict[str, str] = {
    "SPCX": "SpaceX",
    "MSFT": "Microsoft",
    "AAPL": "Apple",
    "NVDA": "NVIDIA",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "UPRO": "UltraPro S&P 500 (3x)",
    "TQQQ": "UltraPro QQQ (3x)",
    "VIXY": "VIX Short-Term Futures",
    "SVXY": "Short VIX Short-Term Futures",
}


class TickerNotWhitelistedError(ValueError):
    """Raised when a ticker outside the approved watchlist is submitted."""


def normalize(ticker: str) -> str:
    """Canonicalise a user/agent-supplied symbol: strip '$', whitespace, upper."""
    return (ticker or "").upper().lstrip("$").strip()


def is_whitelisted(ticker: str) -> bool:
    """True iff `ticker` (after normalisation) is one of the approved ten."""
    return normalize(ticker) in WATCHLIST


def assert_whitelisted(ticker: str) -> str:
    """Return the canonical symbol, or raise TickerNotWhitelistedError.

    This is the strict validation gate (mission Req 1). Used by the request
    schemas so off-list tickers are rejected before any work is started.
    """
    t = normalize(ticker)
    if t not in WATCHLIST:
        raise TickerNotWhitelistedError(
            f"{t or ticker!r} is not on the approved watchlist. "
            f"The desk only processes: {', '.join(WATCHLIST_ORDERED)}."
        )
    return t


def is_volatility_instrument(ticker: str) -> bool:
    """True for VIXY/SVXY — the instruments the Volatility Analyst leads on."""
    return normalize(ticker) in VOL_TICKERS
