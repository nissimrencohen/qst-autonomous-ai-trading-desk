"""Durable store for Continuous Synthesis Loop output (Step 2e).

Holds the LATEST ``ProbabilityReport`` per ticker (so the frontend can poll a
live desk view) plus the loop's round-robin cursor and heartbeat (so a restart
resumes where it left off). SQLite, same lightweight pattern as BriefingStore.

Thread-safety: sqlite serialises writes internally; the loop calls these from
``asyncio.to_thread``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS synthesis_reports (
    ticker       TEXT PRIMARY KEY,
    run_id       TEXT,
    report_json  TEXT NOT NULL,
    macro_json   TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS synthesis_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReportStore:
    def __init__(self, db_path: str = "./data/synthesis_reports.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # Migration for DBs created before macro_json existed.
        try:
            self._conn.execute("ALTER TABLE synthesis_reports ADD COLUMN macro_json TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # column already present
        log.info("ReportStore opened: %s", db_path)

    # ── reports ──────────────────────────────────────────────────────────────

    def save(self, ticker: str, report_dict: dict[str, Any], run_id: str = "",
             macro: dict[str, Any] | None = None) -> None:
        """Upsert the latest report for a ticker (report_dict = ProbabilityReport.model_dump()).

        `macro` is the structured macro+VIX block the loop read from the cache,
        surfaced to the frontend alongside the report (Step 2g data integrity).
        """
        now = _now()
        generated_at = report_dict.get("generated_at", now)
        with self._conn:
            self._conn.execute(
                "INSERT INTO synthesis_reports (ticker, run_id, report_json, macro_json, generated_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET "
                "run_id=excluded.run_id, report_json=excluded.report_json, "
                "macro_json=excluded.macro_json, "
                "generated_at=excluded.generated_at, updated_at=excluded.updated_at",
                (ticker.upper(), run_id, json.dumps(report_dict, default=str),
                 json.dumps(macro or {}, default=str), generated_at, now),
            )

    def get(self, ticker: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM synthesis_reports WHERE ticker = ?", (ticker.upper(),)
        )
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_all(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM synthesis_reports ORDER BY updated_at DESC"
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    # ── loop cursor / heartbeat ────────────────────────────────────────────────

    def _set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO synthesis_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _get_meta(self, key: str, default: str | None = None) -> str | None:
        cur = self._conn.execute("SELECT value FROM synthesis_meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def get_cursor(self) -> int:
        try:
            return int(self._get_meta("cursor", "0"))
        except (TypeError, ValueError):
            return 0

    def set_cursor(self, idx: int) -> None:
        self._set_meta("cursor", str(idx))

    def last_seen(self, ticker: str) -> str | None:
        """Latest ingested_at the loop has already synthesised for this ticker."""
        return self._get_meta(f"last_seen_{ticker.upper()}")

    def mark_seen(self, ticker: str, stamp: str) -> None:
        self._set_meta(f"last_seen_{ticker.upper()}", stamp)

    def record_heartbeat(self, ticker: str, status: str) -> None:
        self._set_meta("last_ticker", ticker.upper())
        self._set_meta("last_status", status)
        self._set_meta("heartbeat", _now())

    def status(self) -> dict[str, Any]:
        return {
            "cursor": self.get_cursor(),
            "last_ticker": self._get_meta("last_ticker"),
            "last_status": self._get_meta("last_status"),
            "heartbeat": self._get_meta("heartbeat"),
            "reports_count": self._conn.execute(
                "SELECT COUNT(*) FROM synthesis_reports"
            ).fetchone()[0],
        }

    # ── internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "ticker": row["ticker"],
            "run_id": row["run_id"],
            "report": json.loads(row["report_json"]),
            "macro": json.loads(row["macro_json"] or "{}"),
            "generated_at": row["generated_at"],
            "updated_at": row["updated_at"],
        }

    def close(self) -> None:
        self._conn.close()
