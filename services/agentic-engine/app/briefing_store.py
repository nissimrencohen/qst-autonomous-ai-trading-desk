"""SQLite store for daily briefings — persists across restarts.

Uses the same SQLite file as agent_memory or a dedicated file.
Table: daily_briefings (date TEXT PK, data JSONB, generated_at TEXT)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS daily_briefings (
    briefing_date TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    data          TEXT NOT NULL
);
"""

_UPSERT = text("""
INSERT INTO daily_briefings (briefing_date, generated_at, data)
VALUES (:date, :generated_at, :data)
ON CONFLICT(briefing_date) DO UPDATE
  SET generated_at = excluded.generated_at,
      data         = excluded.data
""")

_GET_DATE  = text("SELECT data FROM daily_briefings WHERE briefing_date = :date")
_GET_LATEST = text("SELECT data FROM daily_briefings ORDER BY briefing_date DESC LIMIT 1")


class BriefingStore:
    def __init__(self, db_path: str = "./data/agent_memory.db") -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        with self._engine.connect() as conn:
            conn.execute(text(_CREATE))
            conn.commit()
        log.info("BriefingStore ready path=%s", path)

    def save(self, data: dict[str, Any], briefing_date: date | None = None) -> None:
        d = briefing_date or date.today()
        with self._engine.begin() as conn:
            conn.execute(_UPSERT, {
                "date": d.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data": json.dumps(data),
            })

    def get(self, briefing_date: date | None = None) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            if briefing_date:
                row = conn.execute(_GET_DATE, {"date": briefing_date.isoformat()}).fetchone()
            else:
                row = conn.execute(_GET_LATEST).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except Exception as exc:
            log.warning("BriefingStore: failed to deserialize: %s", exc)
            return None
