"""Deterministic rule rails.

Always-on first line of defense (and the complete dev/CI backend). The NeMo
LLM rails run *after* these rules when `GUARDRAILS_BACKEND=nemo` — a request
rejected here never reaches the LLM. Every rule is pure and unit-tested.
"""
from __future__ import annotations

import re

from app.schemas import Violation

# ---------------------------------------------------------------- input rails

_ILLEGAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "insider_information",
        re.compile(r"\b(insider (info|information|tip)s?|non-?public information|mnpi)\b", re.I),
    ),
    (
        "market_manipulation",
        re.compile(r"\b(pump\s*(and|&|n)\s*dump|spoofing|front-?run\w*|wash trad\w+|corner the market)\b", re.I),
    ),
    (
        "sanctioned_or_illicit_asset",
        re.compile(r"\b(sanctioned|ofac[- ]listed|embargoed|dark\s*web|stolen)\b.{0,40}\b(stock|asset|token|coin|securit\w+|compan\w+)\b|\b(launder\w+)\b", re.I),
    ),
]

_FINANCE_SIGNAL = re.compile(
    r"\b(stocks?|shares?|options?|calls?|puts?|strikes?|expiry|earnings|revenue|"
    r"market|trad\w+|invest\w+|portfolio|ticker|price|valuation|backlog|"
    r"volatility|probabilit\w+|bullish|bearish|breakout|support|resistance|"
    r"etf|bond|dividend|margin|short|long)\b",
    re.I,
)

_TICKER_SHAPED = re.compile(r"^[A-Za-z.\-]{1,12}$")


def check_input(question: str, ticker: str | None) -> list[Violation]:
    violations: list[Violation] = []

    for rule, pattern in _ILLEGAL_PATTERNS:
        m = pattern.search(question)
        if m:
            violations.append(
                Violation(rule=rule, detail="Disallowed request category.", excerpt=m.group(0))
            )

    if ticker is not None and not _TICKER_SHAPED.match(ticker):
        violations.append(
            Violation(rule="malformed_ticker", detail=f"Ticker {ticker!r} is not a valid symbol.")
        )

    # off-topic: no ticker provided AND no finance vocabulary in the question
    if ticker is None and not _FINANCE_SIGNAL.search(question):
        violations.append(
            Violation(
                rule="off_topic",
                detail="Request is not about markets, assets, or trading analysis.",
            )
        )
    return violations


# --------------------------------------------------------------- output rails

_GUARANTEE_PHRASES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bguaranteed?( to)?( profits?| returns?| gains?| win| succeed)?\b", re.I), "is likely (not assured)"),
    (re.compile(r"\brisk[- ]free\b", re.I), "lower-risk"),
    (re.compile(r"\bcannot (lose|fail)\b", re.I), "has historically been resilient"),
    (re.compile(r"\bwill (definitely|certainly|surely)\b", re.I), "may"),
    (re.compile(r"\bsure thing\b", re.I), "plausible scenario"),
    (re.compile(r"\b100% (certain|sure|safe)\b", re.I), "not certain"),
]

# claim-like figures only: currency amounts, percentages, or magnitude-suffixed
# numbers ($41.2B, 38%, 22.6B). Bare decimals (e.g. probability 0.55) are the
# engine's own computed outputs, not retrieved claims, and are not checked.
_CLAIM_NUMBER = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?\s?[bmk]?|\d[\d,]*(?:\.\d+)?\s?%|\d[\d,]*(?:\.\d+)?\s?[bm]illion\b)", re.I)


def _normalize_number(token: str) -> str:
    return re.sub(r"[\s,$]", "", token).lower().rstrip(".")


def check_output(text: str, evidence: list[str]) -> tuple[list[Violation], str]:
    """Returns (violations, sanitized_text).

    Guarantee language is sanitizable (replaced with calibrated phrasing);
    hallucinated metrics are not — they make the report unsalvageable.
    """
    violations: list[Violation] = []
    sanitized = text

    for pattern, replacement in _GUARANTEE_PHRASES:
        m = pattern.search(sanitized)
        if m:
            violations.append(
                Violation(rule="absolute_guarantee", detail="Certainty language is not allowed.", excerpt=m.group(0))
            )
            sanitized = pattern.sub(replacement, sanitized)

    if evidence:
        evidence_blob = _normalize_number(" ".join(evidence))
        for m in _CLAIM_NUMBER.finditer(text):
            token = _normalize_number(m.group(0))
            if token not in evidence_blob:
                violations.append(
                    Violation(
                        rule="hallucinated_metric",
                        detail="Figure does not appear in any source document.",
                        excerpt=m.group(0),
                    )
                )
    return violations, sanitized
