"""Background daemon for continuous data ingestion."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
import yfinance as yf
from typing import Any

from app.store import DocumentStore

log = logging.getLogger(__name__)

# V2.0 watchlist — the desk's exact 10 approved instruments (kept in sync with
# services/agentic-engine/app/watchlist.py; rag-service is a separate deployment
# so the list is mirrored here rather than imported). Legacy demo tickers
# (UVXY/ESLT/NXSN/TOND/CUE) have been removed.
ACTIVE_TICKERS = [
    "SPCX", "MSFT", "AAPL", "NVDA", "GOOGL",
    "AMZN", "UPRO", "TQQQ", "VIXY", "SVXY",
]
UPDATE_INTERVAL_SECONDS = 15 * 60

_seen_hashes: set[str] = set()

def _get_hash(ticker: str, title: str, text: str) -> str:
    digest = hashlib.sha1(f"{ticker}|{title}|{text[:200]}".encode()).hexdigest()
    return f"{ticker.upper()}-{digest[:12]}"

def _add_doc_if_new(docs: list, ticker: str, source: str, title: str, body: str, published_at: str | None = None):
    doc_hash = _get_hash(ticker, title, body)
    if doc_hash not in _seen_hashes:
        _seen_hashes.add(doc_hash)
        docs.append({
            "ticker": ticker.upper(),
            "source": source,
            "title": title,
            "text": body,
            "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        })

def fetch_vix_curve() -> str | None:
    try:
        short = yf.Ticker("^VIX9D").fast_info.get("last_price")
        front = yf.Ticker("^VIX").fast_info.get("last_price")
        back = yf.Ticker("^VIX3M").fast_info.get("last_price")
        
        if front is None or back is None:
            return None
            
        spread = float(back) - float(front)
        if abs(spread) < 0.25:
            structure = "flat"
        elif spread > 0:
            structure = "contango"
        else:
            structure = "backwardation"
            
        return (
            f"VIX 9D: {short}\n"
            f"VIX 30D (Front): {front}\n"
            f"VIX 3M (Back): {back}\n"
            f"Spread (Back-Front): {spread:.2f}\n"
            f"Term Structure: {structure}"
        )
    except Exception as e:
        log.warning("VIX curve fetch failed: %s", e)
        return None

def fetch_options_sentiment(tk: yf.Ticker, ticker: str) -> str | None:
    try:
        expiries = tk.options
        if not expiries:
            return None
        chain = tk.option_chain(expiries[0])
        calls, puts = chain.calls, chain.puts
        
        call_oi = float(calls["openInterest"].fillna(0).sum())
        put_oi = float(puts["openInterest"].fillna(0).sum())
        
        pc_oi = round(put_oi / call_oi, 3) if call_oi else None
        
        return (
            f"Nearest Expiry: {expiries[0]}\n"
            f"Put/Call OI Ratio: {pc_oi}\n"
            f"Call OI: {call_oi}\n"
            f"Put OI: {put_oi}"
        )
    except Exception as e:
        log.warning("Options sentiment fetch failed for %s: %s", ticker, e)
        return None

def fetch_ticker_data(ticker: str) -> list[dict[str, Any]]:
    """Fetch recent news and fundamental/technical data for a ticker using yfinance."""
    docs = []
    try:
        tk = yf.Ticker(ticker)
        
        # 1. Fetch News
        try:
            news = tk.news
            for item in news:
                title = item.get("title", "")
                publisher = item.get("publisher", "yfinance")
                link = item.get("link", "")
                pub_time = item.get("providerPublishTime", 0)
                
                if pub_time:
                    published_at = datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat()
                else:
                    published_at = datetime.now(timezone.utc).isoformat()
                
                body = f"News: {title}\nLink: {link}\nPublisher: {publisher}"
                _add_doc_if_new(docs, ticker, publisher, title, body, published_at)
        except Exception as e:
            log.warning("Failed to fetch news for %s: %s", ticker, e)
        
        # 2. Fetch Quote & Fundamentals
        try:
            info = tk.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if price is not None:
                title = f"State of {ticker.upper()} - Market Data"
                body = (
                    f"Price: {price} {info.get('currency', 'USD')}\n"
                    f"Market Cap: {info.get('marketCap')}\n"
                    f"EPS: {info.get('trailingEps')}\n"
                    f"PE: {info.get('trailingPE') or info.get('forwardPE')}\n"
                    f"52W High: {info.get('fiftyTwoWeekHigh')}\n"
                    f"52W Low: {info.get('fiftyTwoWeekLow')}"
                )
                _add_doc_if_new(docs, ticker, "yfinance_quote", title, body)
        except Exception as e:
            log.warning("Failed to fetch quotes for %s: %s", ticker, e)
            
        # 3. Fetch Options Sentiment
        if ticker not in ["^VIX", "^VIX3M", "^VIX9D"]:
            sentiment = fetch_options_sentiment(tk, ticker)
            if sentiment:
                title = f"State of {ticker.upper()} - Options Sentiment"
                _add_doc_if_new(docs, ticker, "yfinance_options", title, sentiment)
                
        # 4. Fetch VIX Curve if applicable
        if ticker == "^VIX":
            curve = fetch_vix_curve()
            if curve:
                title = "State of ^VIX - Term Structure"
                _add_doc_if_new(docs, "^VIX", "yfinance_vix", title, curve)
                
    except Exception as exc:
        log.error("Failed to fetch overall data for %s: %s", ticker, exc)
        
    return docs

async def run_updater(store: DocumentStore):
    """Asynchronous background loop to fetch and ingest data."""
    log.info("RAG Background Updater started.")
    while True:
        try:
            total_added = 0
            for ticker in ACTIVE_TICKERS:
                docs = await asyncio.to_thread(fetch_ticker_data, ticker)
                if docs:
                    added = store.add(docs)
                    total_added += added
                    log.info("[RAG Updater] Found %d new documents for %s. Updating vector store.", added, ticker)

            # Market-wide VIX term structure (macro/fear context). ^VIX is not a
            # tradeable watchlist member, so it is fetched once per cycle here.
            curve = await asyncio.to_thread(fetch_vix_curve)
            if curve:
                vix_docs: list = []
                _add_doc_if_new(vix_docs, "VIX", "yfinance_vix", "VIX Term Structure", curve)
                if vix_docs:
                    total_added += store.add(vix_docs)

            if total_added > 0:
                log.info("[RAG Updater] Cycle complete. Inserted %d documents total.", total_added)
            else:
                log.debug("[RAG Updater] Cycle complete. No new documents found.")
                
        except asyncio.CancelledError:
            log.info("RAG Background Updater cancelled.")
            break
        except Exception as exc:
            log.error("RAG Background Updater encountered an error: %s", exc)
            
        # Sleep for the interval before fetching again
        try:
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            log.info("RAG Background Updater cancelled during sleep.")
            break
