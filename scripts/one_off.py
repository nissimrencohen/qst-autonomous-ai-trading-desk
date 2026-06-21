import asyncio
import logging
import sys

from app.config import settings
from app.ingestion_store import IngestionStore
from app.engine import build_synthesis_engine
from app.synthesis_loop import synthesize_ticker_offline
from app.report_store import ReportStore
from app.runs import build_run_store
from app.offline_tools import offline_macro_snapshot, offline_vix_curve
from app.watchlist import WATCHLIST_ORDERED, normalize

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("one_off")

async def main():
    log.info("Starting one-off CREW synthesis for all 10 tickers...")
    store = IngestionStore(settings.ingestion_db_path)
    engine = build_synthesis_engine(store)
    report_store = ReportStore(settings.synthesis_report_db_path)
    runs = build_run_store()

    for ticker in WATCHLIST_ORDERED:
        log.info(f"===> Synthesizing {ticker} <===")
        try:
            # We must use asyncio.to_thread because synthesize_ticker_offline is blocking
            report = await asyncio.to_thread(
                synthesize_ticker_offline, ticker, settings, engine, runs, store
            )
            if report:
                macro_struct = {"macro": offline_macro_snapshot(store), "vix": offline_vix_curve(store)}
                report_store.save(report.ticker, report.model_dump(), report.run_id, macro=macro_struct)
                log.info(f"Successfully saved {ticker}")
            else:
                log.error(f"Synthesis returned None for {ticker}")
        except Exception as e:
            log.exception(f"Exception synthesizing {ticker}: {e}")

    log.info("Finished one-off synthesis for all tickers.")
    store.close()

if __name__ == "__main__":
    asyncio.run(main())
