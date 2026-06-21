"""PostgreSQL-backed RunStore — durable, multi-worker-safe replacement for the
in-memory OrderedDict store.

Schema is created on first use (CREATE TABLE IF NOT EXISTS). Connection
pooling is handled by SQLAlchemy's built-in QueuePool (default 5+10).

The same `RunHandle` / `RunStore` interface is preserved so zero callers
need to change. Selected via `AGENTIC_RUN_STORE_BACKEND=postgres` +
`AGENTIC_POSTGRES_DSN=postgresql+psycopg2://...`.

Steps are stored as a JSONB array in Postgres so the dashboard trace view
works identically to the in-memory implementation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

from app.schemas import ProbabilityReport, RunTrace

log = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS run_traces (
    run_id          TEXT PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    started_at      TEXT        NOT NULL,
    finished_at     TEXT,
    status          TEXT        NOT NULL DEFAULT 'running',
    steps           JSONB       NOT NULL DEFAULT '[]',
    report          JSONB,
    error           TEXT,
    blocked_reasons JSONB       NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS run_traces_started_at_idx ON run_traces (started_at DESC);
"""

_INSERT = text("""
INSERT INTO run_traces (run_id, ticker, started_at, status, steps, blocked_reasons)
VALUES (:run_id, :ticker, :started_at, 'running', '[]'::jsonb, '[]'::jsonb)
ON CONFLICT (run_id) DO NOTHING
""")

_APPEND_STEP = text("""
UPDATE run_traces
   SET steps = steps || :step::jsonb
 WHERE run_id = :run_id
""")

_SET_FINISHED = text("""
UPDATE run_traces
   SET finished_at = :finished_at
 WHERE run_id = :run_id
""")

_SET_REPORT = text("""
UPDATE run_traces
   SET status      = 'done',
       finished_at = :finished_at,
       report      = :report::jsonb
 WHERE run_id = :run_id
""")

_SET_BLOCKED = text("""
UPDATE run_traces
   SET status          = 'blocked',
       finished_at     = :finished_at,
       blocked_reasons = :blocked_reasons::jsonb
 WHERE run_id = :run_id
""")

_SET_ERROR = text("""
UPDATE run_traces
   SET status      = 'error',
       finished_at = :finished_at,
       error       = :error
 WHERE run_id = :run_id
""")

_GET = text("""
SELECT run_id, ticker, started_at, finished_at, status,
       steps, report, error, blocked_reasons
  FROM run_traces
 WHERE run_id = :run_id
""")

_PRUNE = text("""
DELETE FROM run_traces
 WHERE run_id IN (
     SELECT run_id FROM run_traces
      ORDER BY started_at ASC
      LIMIT :n
 )
""")

_COUNT = text("SELECT COUNT(*) FROM run_traces")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PgRunStore:
    """Postgres-backed RunStore with the same interface as the in-memory store."""

    def __init__(self, dsn: str, max_runs: int = 500) -> None:
        self._engine = create_engine(dsn, pool_pre_ping=True)
        self._max_runs = max_runs
        with self._engine.connect() as conn:
            conn.execute(text(_CREATE_TABLE))
            conn.commit()
        log.info("PgRunStore ready dsn=%s max_runs=%d", dsn.split("@")[-1], max_runs)

    def _prune(self, conn: Any) -> None:
        """Keep at most max_runs rows, deleting the oldest first."""
        row = conn.execute(_COUNT).fetchone()
        excess = (row[0] - self._max_runs) if row else 0
        if excess > 0:
            conn.execute(_PRUNE, {"n": excess})

    def start(self, ticker: str) -> "PgRunHandle":
        import uuid
        run_id = uuid.uuid4().hex[:12]
        with self._engine.begin() as conn:
            conn.execute(_INSERT, {
                "run_id": run_id,
                "ticker": ticker.upper(),
                "started_at": _now(),
            })
            self._prune(conn)
        return PgRunHandle(self, run_id)

    def log(self, run_id: str, step: str, detail: dict) -> None:
        entry = json.dumps({"at": _now(), "step": step, **detail})
        try:
            with self._engine.begin() as conn:
                conn.execute(_APPEND_STEP, {"run_id": run_id, "step": f"[{entry}]"})
        except Exception as exc:
            log.warning("PgRunStore.log failed run_id=%s: %s", run_id, exc)

    def finish(self, run_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(_SET_FINISHED, {"run_id": run_id, "finished_at": _now()})

    def set_report(self, run_id: str, report: ProbabilityReport) -> None:
        with self._engine.begin() as conn:
            conn.execute(_SET_REPORT, {
                "run_id": run_id,
                "finished_at": _now(),
                "report": report.model_dump_json(),
            })

    def set_blocked(self, run_id: str, reasons: list[str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(_SET_BLOCKED, {
                "run_id": run_id,
                "finished_at": _now(),
                "blocked_reasons": json.dumps(reasons),
            })

    def set_error(self, run_id: str, message: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(_SET_ERROR, {
                "run_id": run_id,
                "finished_at": _now(),
                "error": message,
            })

    def get(self, run_id: str) -> RunTrace | None:
        with self._engine.connect() as conn:
            row = conn.execute(_GET, {"run_id": run_id}).fetchone()
        if row is None:
            return None

        run_id_, ticker, started_at, finished_at, status, steps, report_json, error, blocked = row
        report: ProbabilityReport | None = None
        if report_json:
            try:
                data = report_json if isinstance(report_json, dict) else json.loads(report_json)
                report = ProbabilityReport.model_validate(data)
            except Exception as exc:
                log.warning("PgRunStore: failed to deserialize report run_id=%s: %s", run_id_, exc)

        steps_list = steps if isinstance(steps, list) else (json.loads(steps) if steps else [])
        blocked_list = blocked if isinstance(blocked, list) else (json.loads(blocked) if blocked else [])

        return RunTrace(
            run_id=run_id_,
            ticker=ticker,
            started_at=started_at,
            finished_at=finished_at,
            steps=steps_list,
            status=status,
            report=report,
            error=error,
            blocked_reasons=blocked_list,
        )


class PgRunHandle:
    """Same interface as the in-memory RunHandle."""

    def __init__(self, store: PgRunStore, run_id: str) -> None:
        self._store = store
        self.run_id = run_id

    def log(self, step: str, detail: dict) -> None:
        self._store.log(self.run_id, step, detail)
