"""
Live market data seeder — fetches real-time quotes, news, and VIX data
for all active tickers and injects them directly into the RAG service.

Usage:
    python scripts/seed_rag_live.py [--url http://localhost:8001]
"""
from __future__ import annotations

import argparse
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

TICKERS = ["NVDA", "ESLT", "NXSN", "TOND", "CUE", "GOOGL", "SPCX", "^VIX", "UVXY"]
SPACE_TICKERS = ["SPCX", "SPACE"]
VIX_TICKERS = ["^VIX", "^VIX9D", "^VIX3M"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc(ticker: str, source: str, title: str, text: str, published_at: str | None = None) -> dict:
    return {
        "ticker": ticker.upper(),
        "source": source,
        "title": title,
        "text": text,
        "published_at": published_at or _now(),
    }


def fetch_vix_curve() -> list[dict]:
    try:
        import yfinance as yf
        docs = []
        data = {}
        for sym in VIX_TICKERS:
            try:
                fi = yf.Ticker(sym).fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                data[sym] = float(price) if price else None
            except Exception:
                pass

        front = data.get("^VIX")
        back = data.get("^VIX3M")
        short = data.get("^VIX9D")

        if front and back:
            spread = back - front
            structure = "flat" if abs(spread) < 0.25 else ("contango" if spread > 0 else "backwardation")
            regime = "calm" if front < 15 else "elevated" if front < 20 else "stress" if front < 30 else "panic"

            text = (
                f"LIVE VIX Term Structure as of {_now()}\n"
                f"VIX 9D: {short}\n"
                f"VIX 30D (Front): {front}\n"
                f"VIX 3M (Back): {back}\n"
                f"Front-Back Spread: {spread:.2f}\n"
                f"Term Structure: {structure}\n"
                f"Volatility Regime: {regime}\n"
                f"Note: {'Backwardation signals acute near-term fear' if structure == 'backwardation' else 'Contango is normal market state'}"
            )
            docs.append(_doc("^VIX", "yfinance_live", f"Live VIX Term Structure — {structure.upper()} / {regime.upper()}", text))
        return docs
    except Exception as e:
        log.warning("VIX curve fetch failed: %s", e)
        return []


def fetch_ticker(ticker: str) -> list[dict]:
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        docs = []

        # News
        try:
            for item in (tk.news or [])[:5]:
                title = item.get("title", "")
                if not title:
                    continue
                publisher = item.get("publisher", "yfinance")
                link = item.get("link", "")
                pub_time = item.get("providerPublishTime", 0)
                published_at = datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat() if pub_time else _now()
                body = f"News Headline: {title}\nPublisher: {publisher}\nLink: {link}"
                docs.append(_doc(ticker, publisher, title, body, published_at))
                log.info("  [news] %s: %s", ticker, title[:60])
        except Exception as e:
            log.warning("  news fetch failed for %s: %s", ticker, e)

        # Quote & Fundamentals
        try:
            fi = tk.fast_info
            price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            if not price:
                info = tk.info or {}
                price = info.get("regularMarketPrice") or info.get("previousClose")

            if price:
                try:
                    info = tk.info or {}
                except Exception:
                    info = {}
                title = f"Live Market Data — {ticker.upper()}"
                text = (
                    f"Ticker: {ticker.upper()}\n"
                    f"Live Price: {price} {info.get('currency', 'USD')}\n"
                    f"Market Cap: {info.get('marketCap', 'N/A')}\n"
                    f"EPS (TTM): {info.get('trailingEps', 'N/A')}\n"
                    f"P/E Ratio: {info.get('trailingPE') or info.get('forwardPE', 'N/A')}\n"
                    f"52W High: {info.get('fiftyTwoWeekHigh', 'N/A')}\n"
                    f"52W Low: {info.get('fiftyTwoWeekLow', 'N/A')}\n"
                    f"Volume: {info.get('volume', 'N/A')}\n"
                    f"Beta: {info.get('beta', 'N/A')}\n"
                    f"As of: {_now()}"
                )
                docs.append(_doc(ticker, "yfinance_quote", title, text))
                log.info("  [quote] %s: price=%s", ticker, price)
        except Exception as e:
            log.warning("  quote fetch failed for %s: %s", ticker, e)

        return docs
    except Exception as e:
        log.error("Failed to fetch %s: %s", ticker, e)
        return []


def fetch_launch_schedule() -> list[dict]:
    try:
        import requests as req
        resp = req.get(
            "https://ll.thespacedevs.com/2.2.0/launch/upcoming/",
            params={"search": "SpaceX", "limit": 5, "mode": "list"},
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return []
        launches = []
        for r in results:
            launches.append(f"- {r.get('name')} | NET: {r.get('net')} | Status: {(r.get('status') or {}).get('name', 'Unknown')}")
        text = "Upcoming SpaceX Launches (from TheSpaceDevs Launch Library):\n" + "\n".join(launches)
        return [_doc("SPCX", "thespacedevs", "Live SpaceX Launch Schedule", text)]
    except Exception as e:
        log.warning("Launch schedule fetch failed: %s", e)
        return []


def ingest(url: str, docs: list[dict]) -> int:
    if not docs:
        return 0
    resp = httpx.post(f"{url.rstrip('/')}/ingest", json={"documents": docs}, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    return body["ingested"]


def main(url: str) -> None:
    log.info("=== Live RAG Seeder — %d tickers ===", len(TICKERS))
    total = 0

    # VIX curve first
    log.info("Fetching VIX term structure...")
    vix_docs = fetch_vix_curve()
    if vix_docs:
        n = ingest(url, vix_docs)
        total += n
        log.info("  Ingested %d VIX documents", n)

    # SpaceX launches
    log.info("Fetching SpaceX launch schedule...")
    launch_docs = fetch_launch_schedule()
    if launch_docs:
        n = ingest(url, launch_docs)
        total += n
        log.info("  Ingested %d launch schedule documents", n)

    # Per-ticker
    for ticker in TICKERS:
        if ticker in ("^VIX",):
            continue  # already handled above
        log.info("Fetching live data for %s...", ticker)
        docs = fetch_ticker(ticker)
        if docs:
            n = ingest(url, docs)
            total += n
            log.info("  [%s] Ingested %d documents", ticker, n)
        else:
            log.warning("  [%s] No documents fetched", ticker)

    log.info("")
    log.info("=== DONE — Total ingested: %d documents ===", total)

    # Verify
    r = httpx.get(f"{url.rstrip('/')}/health", timeout=10)
    if r.ok:
        log.info("RAG service health: %s", r.json())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    args = ap.parse_args()
    main(args.url)
