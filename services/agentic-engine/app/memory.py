"""Persistent conversational memory for the agentic engine.

Stores per-ticker analysis history so subsequent runs can reference prior
conclusions. Backends:
  sqlite   — SQLAlchemy 2.x on a local file; default for dev/production
  memory   — in-process dict, no persistence; ideal for CI/tests
  dynamodb — AWS DynamoDB; production cloud deployment

Selected via AGENTIC_MEMORY_BACKEND. Swapping backends requires no changes
to callers — the ConversationMemory interface is identical across all three.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from app.config import settings

log = logging.getLogger(__name__)

_MAX_HISTORY = 3  # prior turns injected into each crew run


# ── public interface ──────────────────────────────────────────────────────────

class ConversationMemory(Protocol):
    def load(self, ticker: str) -> list[dict[str, Any]]: ...
    def save(self, ticker: str, entry: dict[str, Any]) -> None: ...


def build_memory() -> ConversationMemory:
    backend = settings.memory_backend
    if backend == "sqlite":
        return _SqliteMemory(settings.memory_db_path)
    if backend == "dynamodb":
        return _DynamoDBMemory(settings.dynamodb_table, settings.dynamodb_region)
    return _InProcessMemory()


def format_prior_context(turns: list[dict[str, Any]]) -> str:
    """Serialise loaded turns into a compact string for task injection."""
    if not turns:
        return "No prior analysis on record for this ticker."
    lines = []
    for t in turns:
        drivers = ", ".join(t.get("key_drivers", [])[:3]) or "—"
        lines.append(
            f"[{t['timestamp'][:10]}] q='{t['question']}' "
            f"bull={t.get('bull_prob', '?'):.0%} "
            f"risk={t.get('risk_level', '?')} "
            f"drivers: {drivers}"
        )
    return "\n".join(lines)


# ── backends ──────────────────────────────────────────────────────────────────

class _InProcessMemory:
    """Ephemeral in-process store. Thread-safe for FastAPI's single-process model."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}

    def load(self, ticker: str) -> list[dict[str, Any]]:
        return self._store.get(ticker.upper(), [])[-_MAX_HISTORY:]

    def save(self, ticker: str, entry: dict[str, Any]) -> None:
        key = ticker.upper()
        self._store.setdefault(key, []).append(entry)
        log.debug("memory[%s] saved turn, total=%d", key, len(self._store[key]))


class _SqliteMemory:
    """SQLAlchemy 2.x / SQLite persistent store."""

    def __init__(self, db_path: str) -> None:
        from pathlib import Path
        from sqlalchemy import Column, Integer, String, Text, create_engine
        from sqlalchemy.orm import DeclarativeBase, Session

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        class Base(DeclarativeBase): ...

        class Turn(Base):
            __tablename__ = "conversation_turns"
            id = Column(Integer, primary_key=True)
            ticker = Column(String(16), nullable=False, index=True)
            timestamp = Column(String(32), nullable=False)
            data_json = Column(Text, nullable=False)

        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(engine)

        self._engine = engine
        self._Session = Session
        self._Turn = Turn

    def load(self, ticker: str) -> list[dict[str, Any]]:
        from sqlalchemy import select, desc

        with self._Session(self._engine) as session:
            rows = (
                session.execute(
                    select(self._Turn)
                    .where(self._Turn.ticker == ticker.upper())
                    .order_by(desc(self._Turn.id))
                    .limit(_MAX_HISTORY)
                )
                .scalars()
                .all()
            )
        # reverse so oldest-first
        return [json.loads(r.data_json) for r in reversed(rows)]

    def save(self, ticker: str, entry: dict[str, Any]) -> None:
        with self._Session(self._engine) as session:
            with session.begin():
                session.add(
                    self._Turn(
                        ticker=ticker.upper(),
                        timestamp=entry.get("timestamp", _now()),
                        data_json=json.dumps(entry),
                    )
                )
        log.debug("sqlite memory saved turn for %s", ticker.upper())


class _DynamoDBMemory:
    """AWS DynamoDB backend (production cloud)."""

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def load(self, ticker: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("ticker").eq(ticker.upper()),
            ScanIndexForward=False,
            Limit=_MAX_HISTORY,
        )
        items = resp.get("Items", [])
        return [json.loads(it["data_json"]) for it in reversed(items)]

    def save(self, ticker: str, entry: dict[str, Any]) -> None:
        ts = entry.get("timestamp", _now())
        self._table.put_item(
            Item={
                "ticker": ticker.upper(),
                "timestamp": ts,
                "data_json": json.dumps(entry),
            }
        )
        log.debug("dynamodb memory saved turn for %s", ticker.upper())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
