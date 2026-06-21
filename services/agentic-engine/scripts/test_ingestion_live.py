"""Live component test for the 1-Minute Continuous Ingestion Engine.

This script runs ONE actual ingestion cycle for a subset of tickers (AAPL, NVDA)
using the real yfinance and Tavily APIs, saving the output to an in-memory SQLite DB,
and prints the results to verify that real-world data parsing works as expected.

Run with:
    cd services/agentic-engine
    ../../.venv/Scripts/python.exe scripts/test_ingestion_live.py
"""
import asyncio
import json
import logging
import time
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.ingestion_store import IngestionStore
from app.ingestion_engine import _run_ingestion_cycle
from app.watchlist import WATCHLIST_ORDERED

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def run_live_test():
    print("\n===================================================================")
    print("LIVE COMPONENT TEST: 1-Minute Ingestion Engine")
    print("===================================================================\n")

    # Use in-memory SQLite for the test
    store = IngestionStore(":memory:")
    
    # We only want to test a couple tickers to save time/bandwidth
    test_tickers = ["AAPL", "NVDA"]
    
    print(f"Targeting tickers: {test_tickers}")
    print("Fetching News, TA signals, Macro, Competitors, and Tavily (if configured)...\n")

    start_time = time.monotonic()

    # We patch the WATCHLIST_ORDERED just for this cycle to restrict the fetch
    with patch("app.ingestion_engine.WATCHLIST_ORDERED", test_tickers):
        # We also prevent the actual push to RAG so we don't pollute the real DB
        with patch("app.ingestion_engine._push_to_rag"):
            await _run_ingestion_cycle(settings, store)

    elapsed = time.monotonic() - start_time
    
    print("\n===================================================================")
    print(f"CYCLE COMPLETE in {elapsed:.2f} seconds")
    print("===================================================================\n")

    # Print results
    total_rows = store.count()
    print(f"Total rows inserted into SQLite: {total_rows}")
    
    for ticker in ["MACRO", "VIX"] + test_tickers:
        rows = store.query_latest(ticker, limit=5)
        print(f"\n--- {ticker} ({len(rows)} rows) ---")
        for r in rows:
            print(f"[{r.source_type.upper()}] {r.title}")
            if r.source_type == "ta_signal":
                # Print the TA payload explicitly
                meta = json.loads(r.meta_json)
                print(f"    RSI: {meta.get('rsi')} ({meta.get('rsi_signal')})")
                print(f"    MACD: {meta.get('macd')} ({meta.get('macd_cross')})")
                print(f"    Price: {meta.get('price')} (BB: {meta.get('bb_position')})")
            elif r.source_type == "macro":
                # Print a summary of macro
                meta = json.loads(r.meta_json)
                if ticker == "MACRO":
                    print(f"    S&P500: {meta.get('sp500', {}).get('price')} ({meta.get('sp500', {}).get('change_pct')}%)")
                else:
                    print(f"    VIX 30d: {meta.get('vix_30d')} (Regime: {meta.get('regime')})")
            else:
                # Just print the first line of the body
                print(f"    {r.body.split(chr(10))[0]}")

    print("\nLive test finished successfully.\n")


if __name__ == "__main__":
    asyncio.run(run_live_test())
