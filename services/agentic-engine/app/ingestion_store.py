"""SQLite-backed structured storage for the 1-minute ingestion engine.

The ingestion engine writes market data (news headlines, TA signals, macro
snapshots, competitor prices) here every 60 seconds. The synthesis loop (Step
2e) reads from this DB to build context for the CrewAI agents — it NEVER
queries live APIs directly.

ChromaDB (via RAG /ingest) gets the same documents for semantic retrieval;
this SQLite layer provides the structured, time-ordered queries the synthesis
loop needs ("give me the latest 5 TA signals for AAPL sorted by timestamp").

Thread safety: sqlite3 in Python serializes writes via its internal lock.
All write/read methods are synchronous — the ingestion engine calls them
from ``asyncio.to_thread()`` so the FastAPI event loop is never blocked.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── data model ────────────────────────────────────────────────────────────────

@dataclass
class IngestionRow:
    """One ingested data point (news headline, TA signal, macro snapshot…)."""
    ticker: str
    source_type: str          # 'quote' | 'news' | 'ta_signal' | 'macro' | 'competitor' | 'social' | 'tavily_news'
    title: str
    body: str
    published_at: str         # ISO-8601
    ingested_at: str = ""     # ISO-8601, set by store if empty
    meta_json: str = "{}"     # arbitrary JSON payload (RSI, MACD, etc.)
    id: str = ""              # SHA1 dedup key, computed by store if empty

    @property
    def meta(self) -> dict[str, Any]:
        """Parse meta_json to dict (convenience)."""
        try:
            return json.loads(self.meta_json)
        except (json.JSONDecodeError, TypeError):
            return {}


def _compute_id(ticker: str, source_type: str, title: str, body: str) -> str:
    """Deterministic dedup key: SHA1(ticker|source_type|title|body[:200])."""
    digest = hashlib.sha1(
        f"{ticker.upper()}|{source_type}|{title}|{body[:200]}".encode()
    ).hexdigest()
    return f"{ticker.upper()}-{source_type}-{digest[:12]}"


# ── store ─────────────────────────────────────────────────────────────────────

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS ingested_data (
    id           TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    published_at TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    meta_json    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ticker_source  ON ingested_data(ticker, source_type);
CREATE INDEX IF NOT EXISTS idx_ingested_at    ON ingested_data(ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticker_time    ON ingested_data(ticker, ingested_at DESC);
"""


class IngestionStore:
    """Lightweight SQLite store for ingested market data.

    Parameters
    ----------
    db_path : str
        Path to the SQLite file, or ``":memory:"`` for tests.
    """

    def __init__(self, db_path: str = "./data/ingestion.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # One shared connection is read+written by several background threads
        # (1-min ingestion writer, 1-s synthesis-loop reader, daily briefing).
        # SQLite forbids concurrent use of a single connection/cursor — without
        # this lock they collide with "bad parameter or other API misuse" /
        # "recursive use of cursors". Serialise every connection access.
        self._lock = threading.RLock()
        log.info("IngestionStore opened: %s", db_path)

    # ── write ──────────────────────────────────────────────────────────────

    def upsert(self, rows: list[IngestionRow]) -> int:
        """Insert rows, skipping duplicates (by ID). Returns count of NEW rows."""
        if not rows:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_count = 0
        with self._lock, self._conn:
            for r in rows:
                rid = r.id or _compute_id(r.ticker, r.source_type, r.title, r.body)
                ingested_at = r.ingested_at or now_iso
                try:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO ingested_data "
                        "(id, ticker, source_type, title, body, published_at, ingested_at, meta_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (rid, r.ticker.upper(), r.source_type, r.title,
                         r.body, r.published_at, ingested_at, r.meta_json),
                    )
                    if self._conn.execute("SELECT changes()").fetchone()[0] > 0:
                        new_count += 1
                except sqlite3.Error as exc:
                    log.warning("IngestionStore upsert failed for %s: %s", rid, exc)
        return new_count

    # ── read ───────────────────────────────────────────────────────────────

    def query_latest(
        self,
        ticker: str,
        source_type: str | None = None,
        limit: int = 10,
    ) -> list[IngestionRow]:
        """Most recent rows for a ticker, optionally filtered by source_type."""
        with self._lock:
            if source_type:
                cur = self._conn.execute(
                    "SELECT * FROM ingested_data "
                    "WHERE ticker = ? AND source_type = ? "
                    "ORDER BY ingested_at DESC LIMIT ?",
                    (ticker.upper(), source_type, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM ingested_data "
                    "WHERE ticker = ? "
                    "ORDER BY ingested_at DESC LIMIT ?",
                    (ticker.upper(), limit),
                )
            return [self._row_to_obj(r) for r in cur.fetchall()]

    def query_since(
        self,
        ticker: str,
        since: datetime,
        source_type: str | None = None,
    ) -> list[IngestionRow]:
        """All rows for ``ticker`` ingested after ``since``."""
        since_iso = since.isoformat(timespec="seconds")
        with self._lock:
            if source_type:
                cur = self._conn.execute(
                    "SELECT * FROM ingested_data "
                    "WHERE ticker = ? AND source_type = ? AND ingested_at > ? "
                    "ORDER BY ingested_at DESC",
                    (ticker.upper(), source_type, since_iso),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM ingested_data "
                    "WHERE ticker = ? AND ingested_at > ? "
                    "ORDER BY ingested_at DESC",
                    (ticker.upper(), since_iso),
                )
            return [self._row_to_obj(r) for r in cur.fetchall()]

    # ── maintenance ────────────────────────────────────────────────────────

    def prune(self, older_than_hours: int = 48) -> int:
        """Delete rows older than ``older_than_hours``. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM ingested_data WHERE ingested_at < ?", (cutoff,)
            )
            deleted = self._conn.execute("SELECT changes()").fetchone()[0]
        if deleted:
            log.info("IngestionStore pruned %d rows older than %dh", deleted, older_than_hours)
        return deleted

    def count(self, ticker: str | None = None) -> int:
        """Total rows, optionally filtered by ticker."""
        with self._lock:
            if ticker:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM ingested_data WHERE ticker = ?",
                    (ticker.upper(),),
                )
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM ingested_data")
            return cur.fetchone()[0]

    def stats(self) -> dict[str, Any]:
        """Aggregate snapshot for the Ingestion Dashboard."""
        with self._lock:
            by_source = {
                row["source_type"]: row["c"]
                for row in self._conn.execute(
                    "SELECT source_type, COUNT(*) c FROM ingested_data GROUP BY source_type"
                ).fetchall()
            }
            by_ticker = [
                {"ticker": row["ticker"], "rows": row["c"], "latest": row["m"]}
                for row in self._conn.execute(
                    "SELECT ticker, COUNT(*) c, MAX(ingested_at) m "
                    "FROM ingested_data GROUP BY ticker ORDER BY ticker"
                ).fetchall()
            ]
            latest = self._conn.execute(
                "SELECT MAX(ingested_at) FROM ingested_data"
            ).fetchone()[0]
        return {
            "total": self.count(),
            "by_source_type": by_source,
            "by_ticker": by_ticker,
            "latest_ingested_at": latest,
        }

    # ── internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_obj(row: sqlite3.Row) -> IngestionRow:
        return IngestionRow(
            id=row["id"],
            ticker=row["ticker"],
            source_type=row["source_type"],
            title=row["title"],
            body=row["body"],
            published_at=row["published_at"],
            ingested_at=row["ingested_at"],
            meta_json=row["meta_json"],
        )

    def close(self) -> None:
        self._conn.close()
