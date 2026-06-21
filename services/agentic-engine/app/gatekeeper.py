"""Execution Gatekeeper — whitelist enforcement and broker routing.

Sits between the CrewAI synthesis output and any live/paper order. The
analysis report always flows through; only the ExecutionPlan leg is
gated. Non-whitelisted tickers are blocked, logged to Langfuse, and
surfaced as a dashboard toast (via the report's `execution_blocked`
metadata field).

Whitelist (canonical uppercase, checked case-insensitively) — the single
source of truth lives in app.watchlist:
  SPCX  MSFT  AAPL  NVDA  GOOGL  AMZN  UPRO  TQQQ  VIXY  SVXY

Broker support:
  • Alpaca (paper + live) — set AGENTIC_ALPACA_KEY + AGENTIC_ALPACA_SECRET.
    AGENTIC_ALPACA_PAPER=true (default) routes to Alpaca's paper endpoint.
  • Stub — when no broker key is configured, orders are logged only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.config import settings
from app.schemas import ExecutionPlan, ProbabilityReport
# Re-exported for backward compatibility — the canonical set lives in watchlist.
from app.watchlist import WATCHLIST as WHITELIST, is_whitelisted

log = logging.getLogger(__name__)


# ── order result ─────────────────────────────────────────────────────────────

OrderStatus = Literal["submitted", "filled", "rejected", "stub", "blocked"]


@dataclass
class OrderResult:
    ticker: str
    side: str
    order_type: str
    entry: float | None
    broker: str
    status: OrderStatus
    broker_order_id: str | None = None
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


# ── broker backends ──────────────────────────────────────────────────────────

def _submit_alpaca(ticker: str, plan: ExecutionPlan) -> OrderResult:
    """Submit a paper (or live) order to Alpaca via alpaca-trade-api."""
    try:
        import alpaca_trade_api as tradeapi  # type: ignore[import-untyped]
    except ImportError:
        return OrderResult(
            ticker=ticker, side=plan.side, order_type=plan.order_type,
            entry=plan.entry, broker="alpaca",
            status="stub", message="alpaca-trade-api not installed — order logged only",
        )

    key = settings.alpaca_key.get_secret_value()
    secret = settings.alpaca_secret.get_secret_value()
    base_url = settings.alpaca_base_url

    api = tradeapi.REST(key, secret, base_url, api_version="v2")
    alpaca_side = "buy" if plan.side == "long" else "sell"

    try:
        if plan.order_type == "market" or plan.entry is None:
            order = api.submit_order(
                symbol=ticker,
                qty=1,
                side=alpaca_side,
                type="market",
                time_in_force="day",
            )
        else:
            order = api.submit_order(
                symbol=ticker,
                qty=1,
                side=alpaca_side,
                type="limit",
                time_in_force="day",
                limit_price=str(round(plan.entry, 4)),
            )
        return OrderResult(
            ticker=ticker, side=plan.side, order_type=plan.order_type,
            entry=plan.entry, broker="alpaca",
            status="submitted", broker_order_id=str(order.id),
            message=f"Alpaca order {order.id} submitted ({order.status})",
        )
    except Exception as exc:
        log.error("Alpaca order submission failed ticker=%s: %s", ticker, exc)
        return OrderResult(
            ticker=ticker, side=plan.side, order_type=plan.order_type,
            entry=plan.entry, broker="alpaca",
            status="rejected", message=str(exc),
        )


def _submit_stub(ticker: str, plan: ExecutionPlan) -> OrderResult:
    """No-op broker — logs the order but does not connect to any API."""
    log.info(
        "STUB ORDER ticker=%s side=%s type=%s entry=%s",
        ticker, plan.side, plan.order_type, plan.entry,
    )
    return OrderResult(
        ticker=ticker, side=plan.side, order_type=plan.order_type,
        entry=plan.entry, broker="stub",
        status="stub", message="No broker configured — order logged only (paper mode)",
    )


def _route_to_broker(ticker: str, plan: ExecutionPlan) -> OrderResult:
    """Select broker backend based on config."""
    if settings.alpaca_key.get_secret_value():
        return _submit_alpaca(ticker, plan)
    return _submit_stub(ticker, plan)


# ── Langfuse violation logger ─────────────────────────────────────────────────

def _log_violation_to_langfuse(ticker: str, run_id: str, reasons: list[str]) -> None:
    try:
        from app.langfuse_tracing import build_langfuse_client
        lf = build_langfuse_client()
        if lf is None:
            return
        lf.trace(
            name="gatekeeper_violation",
            input={"ticker": ticker, "run_id": run_id},
            output={"blocked": True, "reasons": reasons},
            tags=["gatekeeper", "whitelist_violation"],
        )
        lf.flush()
    except Exception as exc:
        log.debug("langfuse violation log skipped: %s", exc)


# ── public API ────────────────────────────────────────────────────────────────

@dataclass
class GatekeeperResult:
    """Returned from `enforce()`. Always carries the (possibly annotated) report."""
    report: ProbabilityReport
    execution_allowed: bool
    order: OrderResult | None = None
    violation_reasons: list[str] = field(default_factory=list)


def enforce(report: ProbabilityReport, run_id: str) -> GatekeeperResult:
    """Enforce whitelist + broker routing on the execution plan.

    Analysis is never blocked — only the ExecutionPlan leg is gated.
    Returns the report with an extra caveat when execution is blocked.
    """
    plan = report.execution_plan
    if plan is None or plan.side == "flat":
        # Nothing to execute — pass through unchanged.
        return GatekeeperResult(report=report, execution_allowed=True)

    ticker = report.ticker.upper()

    if not is_whitelisted(ticker):
        reasons = [
            f"{ticker} is not on the approved instrument whitelist "
            f"({', '.join(sorted(WHITELIST))}).",
            "Execution has been BLOCKED. Analysis is retained for review only.",
        ]
        _log_violation_to_langfuse(ticker, run_id, reasons)
        log.warning(
            "GATEKEEPER BLOCKED execution ticker=%s run_id=%s reasons=%s",
            ticker, run_id, reasons,
        )
        blocked_report = report.model_copy(
            update={
                "caveats": [
                    *report.caveats,
                    f"[GATEKEEPER] Execution BLOCKED — {ticker} is not on the "
                    "approved whitelist. Signal is informational only.",
                ],
                "execution_plan": plan.model_copy(update={"paper_only": True}),
            }
        )
        return GatekeeperResult(
            report=blocked_report,
            execution_allowed=False,
            violation_reasons=reasons,
        )

    # Whitelisted — route to broker.
    order = _route_to_broker(ticker, plan)
    log.info(
        "GATEKEEPER ALLOWED execution ticker=%s run_id=%s broker=%s status=%s",
        ticker, run_id, order.broker, order.status,
    )
    return GatekeeperResult(report=report, execution_allowed=True, order=order)
