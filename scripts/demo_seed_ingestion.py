"""Seed the ingestion cache with realistic rows for the live demo.

Sandbox Yahoo Finance egress is unreliable (libcurl timeouts, documented in
Step 2d), so we pre-populate the ingestion DB the synthesis loop reads from —
the real ingestion engine still runs alongside and augments this best-effort.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "agentic-engine"))

from app.ingestion_store import IngestionRow, IngestionStore  # noqa: E402
from app.watchlist import WATCHLIST_ORDERED, competitors_for  # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 else "./data/demo_ingestion.db"
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
random.seed(7)

store = IngestionStore(DB)
rows: list[IngestionRow] = [
    IngestionRow("MACRO", "macro", f"Broad Market Snapshot @ {now}", "macro", now,
                 meta_json=json.dumps({"sp500": {"symbol": "^GSPC", "price": 7420.10, "change_pct": -1.21},
                                       "nasdaq": {"symbol": "^IXIC", "price": 26021.70, "change_pct": -1.34},
                                       "market_tone": "risk-off (broad selloff)"})),
    IngestionRow("VIX", "macro", f"VIX Term Structure @ {now}", "vix", now,
                 meta_json=json.dumps({"vix_9d": 18.4, "vix_30d": 17.10, "vix_3m": 18.9,
                                       "term_structure": "contango", "regime": "elevated"})),
]

for i, t in enumerate(WATCHLIST_ORDERED):
    price = round(80 + i * 41.3 + random.uniform(-6, 6), 2)
    rsi = round(random.uniform(34, 73), 1)
    rows.append(IngestionRow(t, "quote", f"Quote: {t} @ {now}", "q", now,
                meta_json=json.dumps({"ticker": t, "price": price,
                                      "eps": round(random.uniform(2, 12), 2),
                                      "pe": round(random.uniform(15, 45), 1),
                                      "market_cap": random.randint(60, 3200) * 10**9})))
    rows.append(IngestionRow(t, "ta_signal", f"TA Signals: {t} @ {now}", "ta", now,
                meta_json=json.dumps({"rsi": rsi,
                                      "rsi_signal": "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral",
                                      "macd_cross": random.choice(["bullish", "bearish", "neutral"]),
                                      "bb_position": random.choice(["upper_half", "lower_half", "above_upper"]),
                                      "price": price})))
    peers = list(competitors_for(t))[:3]
    rows.append(IngestionRow(t, "competitor", f"Peers: {t} @ {now}", "p", now,
                meta_json=json.dumps({"ticker": t, "peers": [
                    {"ticker": p, "price": round(random.uniform(40, 520), 2),
                     "change_pct": round(random.uniform(-2.2, 2.2), 2)} for p in peers]})))
    rows.append(IngestionRow(t, "news", f"{t}: sector rotation as macro turns risk-off", f"News body for {t}.", now,
                meta_json=json.dumps({"publisher": "DemoWire"})))

n = store.upsert(rows)
print(f"seeded {n} new rows into {DB} for {len(WATCHLIST_ORDERED)} tickers + MACRO + VIX (total={store.count()})")
store.close()
