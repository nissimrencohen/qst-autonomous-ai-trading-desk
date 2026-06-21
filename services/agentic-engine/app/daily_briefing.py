"""Daily Morning Briefing — runs automatically at market open.

Schedule: Every weekday at 09:00 AM ET (14:00 UTC winter / 13:00 UTC summer).
Can also be triggered on-demand via POST /daily-briefing/trigger.

What it does
────────────
1. Fetches live VIX level + regime (yfinance).
2. Fetches overnight gap % for all 10 whitelisted instruments.
3. Fires POST /analyze/batch with a "morning briefing" question tuned for
   high % move probability signals and short-horizon directional calls.
4. For each completed report, runs move_probability.instrument_probs() to
   compute P(>1%), P(>2%), P(>3%), P(>5%), P(>10%) for today + 5 days.
5. Saves the full briefing to BriefingStore (SQLite, survives restarts).

The question template is specifically engineered for morning-briefing use:
  horizon_days = 5 (this week — sweet spot at 58–71% signal effectiveness).
  question     = "Morning Briefing …" with VIX/gap/regime injected into
                 macro_context so every CrewAI agent sees it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Any

from app.briefing_store import BriefingStore
from app.config import settings
from app.move_probability import instrument_probs, vix_implied_probs
from app.watchlist import WATCHLIST_ORDERED

log = logging.getLogger(__name__)

# ── US market holidays (major; expand annually) ───────────────────────────────
_US_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 11, 27), # Day after Thanksgiving (early close → skip)
    date(2026, 12, 25), # Christmas
}

# All ten approved instruments (canonical order from the watchlist).
_BRIEFING_TICKERS = list(WATCHLIST_ORDERED)

# Briefing fires at 09:00 ET = 14:00 UTC (EST) / 13:00 UTC (EDT)
_BRIEFING_HOUR_UTC_WINTER = 14   # EST (UTC-5)
_BRIEFING_HOUR_UTC_SUMMER = 13   # EDT (UTC-4)


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _US_HOLIDAYS_2026


def _briefing_hour_utc(d: date) -> int:
    """Rough DST: EDT Apr–Oct, EST Nov–Mar."""
    month = d.month
    if 4 <= month <= 10:
        return _BRIEFING_HOUR_UTC_SUMMER
    return _BRIEFING_HOUR_UTC_WINTER


def _next_briefing_utc() -> datetime:
    """Next scheduled briefing datetime (UTC)."""
    now = datetime.now(timezone.utc)
    for offset in range(7):  # search up to 7 days ahead
        d = (now + timedelta(days=offset)).date()
        if not _is_trading_day(d):
            continue
        trigger = datetime(d.year, d.month, d.day,
                           _briefing_hour_utc(d), 0, 0, tzinfo=timezone.utc)
        if trigger > now:
            return trigger
    # Fallback: 24 h from now (should never happen)
    return now + timedelta(hours=24)


# ── market context helpers ─────────────────────────────────────────────────────

def _fetch_vix() -> tuple[float, str]:
    """Returns (vix_level, regime_str)."""
    try:
        import yfinance as yf
        fi = yf.Ticker("^VIX").fast_info
        vix = float(getattr(fi, "last_price", 16.0) or 16.0)
        if vix < 15:   regime = "calm"
        elif vix < 20: regime = "elevated"
        elif vix < 30: regime = "stress"
        else:          regime = "panic"
        return vix, regime
    except Exception as exc:
        log.warning("VIX fetch failed: %s", exc)
        return 16.0, "elevated"


def _fetch_overnight_gaps() -> dict[str, float]:
    """Overnight gap % = (today_open - prev_close) / prev_close * 100."""
    gaps: dict[str, float] = {}
    try:
        import yfinance as yf
        for ticker in _BRIEFING_TICKERS:
            try:
                hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
                if len(hist) >= 2:
                    prev_close = float(hist["Close"].iloc[-2])
                    today_open = float(hist["Open"].iloc[-1])
                    if prev_close > 0:
                        gaps[ticker] = round((today_open - prev_close) / prev_close * 100, 2)
            except Exception:
                pass
    except Exception as exc:
        log.warning("overnight gap fetch failed: %s", exc)
    return gaps


def _fetch_30min_data() -> dict[str, dict]:
    """First 30-min OHLCV for each instrument (5m bars × 6)."""
    bars: dict[str, dict] = {}
    try:
        import yfinance as yf
        for ticker in _BRIEFING_TICKERS:
            try:
                hist = yf.Ticker(ticker).history(period="1d", interval="5m", prepost=False)
                if len(hist) >= 6:
                    first6 = hist.iloc[:6]
                    lo, hi = float(first6["Low"].min()), float(first6["High"].max())
                    op = float(first6["Open"].iloc[0])
                    cl = float(first6["Close"].iloc[-1])
                    vol = int(first6["Volume"].sum())
                    range_pct = round((hi - lo) / op * 100, 2) if op > 0 else 0.0
                    mom_pct   = round((cl - op) / op * 100, 2) if op > 0 else 0.0
                    bars[ticker] = {
                        "open": round(op, 2), "low": round(lo, 2),
                        "high": round(hi, 2), "close": round(cl, 2),
                        "volume": vol, "range_pct": range_pct, "momentum_pct": mom_pct,
                    }
            except Exception:
                pass
    except Exception as exc:
        log.warning("30min data fetch failed: %s", exc)
    return bars


# ── macro context builder ─────────────────────────────────────────────────────

def _build_macro_context(vix: float, regime: str, gaps: dict, bars: dict) -> str:
    lines = [
        f"MORNING BRIEFING CONTEXT — {date.today().isoformat()} 09:00 AM ET",
        f"VIX: {vix:.1f} ({regime.upper()})",
        "",
        "Overnight gaps (prev close → today open):",
    ]
    for t in _BRIEFING_TICKERS:
        g = gaps.get(t)
        b = bars.get(t, {})
        gap_str = f"{g:+.2f}%" if g is not None else "n/a"
        rng_str = f"range {b.get('range_pct', 0):.1f}% mom {b.get('momentum_pct', 0):+.2f}%" \
                  if b else "no intraday data"
        lines.append(f"  {t:6s}: gap {gap_str:<8s} | 30-min {rng_str}")
    lines += [
        "",
        "Focus: assign explicit probability estimates (e.g. '65% probability of an up-move "
        ">2% by Friday') to each directional call. Flag binary risk events this week.",
    ]
    return "\n".join(lines)


def _build_morning_question(ticker: str, vix: float, regime: str, gap: float | None) -> str:
    gap_str = f"Overnight gap: {gap:+.2f}%. " if gap is not None else ""
    return (
        f"MORNING BRIEFING: What is the probability and magnitude of a significant price "
        f"move (>2%, >5%) in {ticker} today and over the next 5 trading days? "
        f"{gap_str}VIX at {vix:.1f} ({regime}). "
        f"Provide: (1) directional bias with explicit probability estimate, "
        f"(2) key support/resistance levels for today's session, "
        f"(3) probability of a >2% move in either direction today, "
        f"(4) options-flow bias if available, "
        f"(5) the most important risk event this week for {ticker}."
    )


# ── offline market context ────────────────────────────────────────────────────

def _vix_from_store_or_live(store) -> tuple[float, str]:
    """VIX level + regime from the ingestion cache (offline); live fallback."""
    try:
        rows = store.query_latest("VIX", "macro", 1)
        if rows:
            m = rows[0].meta
            v = m.get("vix_30d")
            if v is not None:
                v = float(v)
                regime = m.get("regime") or (
                    "calm" if v < 15 else "elevated" if v < 20 else "stress" if v < 30 else "panic"
                )
                return v, regime
    except Exception as exc:
        log.warning("daily_briefing: VIX-from-cache failed (%s); using live", exc)
    return _fetch_vix()


# ── core briefing runner ──────────────────────────────────────────────────────

async def run_daily_briefing(engine, runs, briefing_store: BriefingStore) -> dict[str, Any]:
    """Execute the morning briefing pipeline — OFFLINE (Bug #1 fix).

    Reads the ingestion cache and runs the offline crew (no rag-service / live
    orchestrator), so the briefing never errors when external services are down.
    Move-probabilities still use historical vol from yfinance (degrade-safe).
    The `engine` argument is retained for signature compatibility; the briefing
    builds its own cache-bound offline engine.
    """
    log.info("daily_briefing: starting OFFLINE morning briefing for %s", date.today().isoformat())

    from app.engine import build_synthesis_engine
    from app.ingestion_store import IngestionStore
    from app.synthesis_loop import synthesize_ticker_offline

    store = IngestionStore(settings.ingestion_db_path)
    offline_engine = build_synthesis_engine(store)

    # VIX from cache (offline); gaps / 30-min bars are best-effort market data
    # (yfinance — NOT the rag-service / orchestrator dependency that errored).
    vix, regime = _vix_from_store_or_live(store)
    gaps = _fetch_overnight_gaps()
    bars = _fetch_30min_data()

    vix_probs_1d = vix_implied_probs(vix, "SPY", horizon_trading_days=1)
    vix_probs_5d = vix_implied_probs(vix, "SPY", horizon_trading_days=5)

    # Sequential offline synthesis per ticker (mirrors the continuous loop).
    instruments: list[dict] = []
    for ticker in _BRIEFING_TICKERS:
        question = _build_morning_question(ticker, vix, regime, gaps.get(ticker))
        report = await asyncio.to_thread(
            synthesize_ticker_offline, ticker, settings, offline_engine, runs, store,
            horizon_days=5, question=question,
        )
        status = "done" if report is not None else "error"
        crew_bull = report.probabilities.bullish if report else 0.0
        crew_bear = report.probabilities.bearish if report else 0.0

        try:
            probs_1d = instrument_probs(
                ticker=ticker, crew_bullish=crew_bull, crew_bearish=crew_bear,
                vix_level=vix, horizon_trading_days=1,
            )
            probs_5d = instrument_probs(
                ticker=ticker, crew_bullish=crew_bull, crew_bearish=crew_bear,
                vix_level=vix, horizon_trading_days=5,
            )
        except Exception as exc:
            log.warning("daily_briefing: move_probs failed for %s: %s", ticker, exc)
            probs_1d = probs_5d = {}

        instruments.append({
            "ticker": ticker,
            "run_id": report.run_id if report else "",
            "status": status,
            "overnight_gap_pct": gaps.get(ticker),
            "intraday_30m": bars.get(ticker),
            "crew": {
                "bullish": round(crew_bull, 3),
                "neutral": round(report.probabilities.neutral, 3) if report else None,
                "bearish": round(crew_bear, 3),
                "confidence": round(report.confidence, 3) if report else None,
                "risk_level": report.risk_assessment.risk_level if report else None,
                "max_position_pct": report.risk_assessment.max_position_pct if report else None,
                "execution_side": report.execution_plan.side if (report and report.execution_plan) else "flat",
                "entry": report.execution_plan.entry if (report and report.execution_plan) else None,
                "stop_loss": report.execution_plan.stop_loss if (report and report.execution_plan) else None,
                "target": report.execution_plan.target if (report and report.execution_plan) else None,
                "risk_reward": report.execution_plan.risk_reward_ratio if (report and report.execution_plan) else None,
            },
            "move_probs_1d": probs_1d,
            "move_probs_5d": probs_5d,
        })

    store.close()

    briefing: dict[str, Any] = {
        "briefing_date": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_context": {
            "vix": round(vix, 2),
            "regime": regime,
            "vix_implied_1d": vix_probs_1d,
            "vix_implied_5d": vix_probs_5d,
        },
        "instruments": instruments,
        "engine_backend": offline_engine.name,
        "data_source": "ingestion_cache (offline)",
        "status": "complete",
    }

    briefing_store.save(briefing)
    log.info(
        "daily_briefing: complete tickers=%d vix=%.1f regime=%s",
        len(instruments), vix, regime,
    )
    return briefing


# ── background scheduler ──────────────────────────────────────────────────────

async def _scheduler_loop(engine, runs, briefing_store: BriefingStore) -> None:
    """Asyncio background task that fires run_daily_briefing at 09:00 AM ET."""
    log.info("daily_briefing: scheduler started")
    while True:
        try:
            next_fire = _next_briefing_utc()
            now = datetime.now(timezone.utc)
            wait_s = (next_fire - now).total_seconds()
            log.info(
                "daily_briefing: next fire at %s UTC (in %.0f min)",
                next_fire.strftime("%Y-%m-%d %H:%M"),
                wait_s / 60,
            )
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            await run_daily_briefing(engine, runs, briefing_store)
        except asyncio.CancelledError:
            log.info("daily_briefing: scheduler cancelled")
            break
        except Exception as exc:
            log.exception("daily_briefing: scheduler error: %s — retrying in 60s", exc)
            await asyncio.sleep(60)


def start_briefing_scheduler(engine, runs, briefing_store: BriefingStore) -> asyncio.Task:
    return asyncio.create_task(_scheduler_loop(engine, runs, briefing_store))


def stop_briefing_scheduler(task: asyncio.Task) -> None:
    task.cancel()
