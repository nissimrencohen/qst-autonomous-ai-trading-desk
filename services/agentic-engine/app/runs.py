"""Run-trace store — in-memory (default) or PostgreSQL (v1.4 durable mode).

Select via AGENTIC_RUN_STORE_BACKEND:
  "memory"   (default) — in-process OrderedDict, lost on restart.
  "postgres" — durable, multi-worker-safe; requires AGENTIC_POSTGRES_DSN.

`build_run_store()` is the public factory used by main.py.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from app.config import settings
from app.schemas import RunTrace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunHandle:
    def __init__(self, store: "RunStore", run_id: str) -> None:
        self._store = store
        self.run_id = run_id

    def log(self, step: str, detail: dict) -> None:
        self._store.log(self.run_id, step, detail)


class RunStore:
    def __init__(self, max_runs: int | None = None) -> None:
        self._max_runs = max_runs or settings.max_runs
        self._runs: OrderedDict[str, RunTrace] = OrderedDict()
        self._lock = threading.Lock()

    def start(self, ticker: str) -> RunHandle:
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._runs[run_id] = RunTrace(
                run_id=run_id,
                ticker=ticker.upper(),
                started_at=_now(),
                finished_at=None,
                steps=[],
            )
            while len(self._runs) > self._max_runs:
                self._runs.popitem(last=False)
        return RunHandle(self, run_id)

    def log(self, run_id: str, step: str, detail: dict) -> None:
        with self._lock:
            trace = self._runs.get(run_id)
            if trace is not None:
                trace.steps.append({"at": _now(), "step": step, **detail})

    def finish(self, run_id: str) -> None:
        with self._lock:
            trace = self._runs.get(run_id)
            if trace is not None:
                trace.finished_at = _now()

    def set_report(self, run_id: str, report) -> None:
        """Attach the finished report and mark the run done (async /analyze)."""
        with self._lock:
            trace = self._runs.get(run_id)
            if trace is not None:
                trace.report = report
                trace.status = "done"
                trace.finished_at = _now()

    def set_blocked(self, run_id: str, reasons: list[str]) -> None:
        with self._lock:
            trace = self._runs.get(run_id)
            if trace is not None:
                trace.status = "blocked"
                trace.blocked_reasons = reasons
                trace.finished_at = _now()

    def set_error(self, run_id: str, message: str) -> None:
        with self._lock:
            trace = self._runs.get(run_id)
            if trace is not None:
                trace.status = "error"
                trace.error = message
                trace.finished_at = _now()

    def get(self, run_id: str) -> RunTrace | None:
        with self._lock:
            return self._runs.get(run_id)


# ── factory ──────────────────────────────────────────────────────────────────

def build_run_store():
    """Return the appropriate RunStore backend based on config."""
    from app.config import settings  # local import to avoid circular at module load
    backend = getattr(settings, "run_store_backend", "memory")
    if backend == "postgres":
        from app.runs_pg import PgRunStore
        dsn = settings.postgres_dsn.get_secret_value() if hasattr(settings.postgres_dsn, "get_secret_value") else settings.postgres_dsn
        return PgRunStore(dsn=dsn, max_runs=settings.max_runs)
    return RunStore(max_runs=settings.max_runs)
