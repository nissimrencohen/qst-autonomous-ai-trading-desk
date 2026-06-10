"""In-memory run-trace store.

Keeps the last `AGENTIC_MAX_RUNS` agent executions so the dashboard can show
the reasoning chain via GET /runs/{run_id}. Process-local by design — traces
are observability data, not system of record.
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

    def get(self, run_id: str) -> RunTrace | None:
        with self._lock:
            return self._runs.get(run_id)
