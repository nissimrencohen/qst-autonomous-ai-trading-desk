"""Chat persistence layer — dual-storage for the Conversational Assistant.

Two responsibilities:
1. ChatStore  — raw transcript persistence (SQLite) for UI rendering.
   Every user/assistant turn is written here unconditionally.

2. InsightExtractor — filters assistant responses and extracts high-value
   financial insights into ChromaDB.  Heuristic-first (no LLM cost) with
   a Groq/litellm confirmation step only for candidate turns.

SQLite tables
─────────────
  chat_sessions  (session_id PK, created_at, title)
  chat_messages  (id, session_id FK, role, content, created_at, tokens_est, model_used)

ChromaDB collection
───────────────────
  "assistant_insights"
    document  = extracted insight sentence(s)
    metadata  = {ticker, session_id, timestamp, source:"chat", model_used}
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── financial keyword gate ────────────────────────────────────────────────────

_FINANCIAL_KEYWORDS: frozenset[str] = frozenset(
    [
        "price", "signal", "risk", "entry", "stop", "loss", "target",
        "bullish", "bearish", "neutral", "breakout", "resistance", "support",
        "volume", "momentum", "volatility", "vix", "earnings", "revenue",
        "margin", "position", "portfolio", "allocation", "exposure",
        "p/l", "p&l", "pnl", "return", "drawdown", "sharpe", "beta",
        "alpha", "correlation", "hedge", "strategy", "technical", "fundamental",
        "ma20", "ma50", "rsi", "macd", "atr", "pattern", "regime",
        "contango", "backwardation", "spread", "options", "puts", "calls",
        "sector", "catalyst", "macro", "fed", "rate", "inflation",
        "gdp", "sentiment", "flow", "institutional", "liquidity",
    ]
)

_NOISE_PREFIXES: tuple[str, ...] = (
    "hello", "hi ", "hey ", "sure", "of course", "i understand", "i see",
    "you're welcome", "thank you", "thanks", "no problem", "great question",
    "i apologize", "i'm sorry", "sorry about", "let me know if",
    "feel free to", "is there anything else",
)

_MIN_CONTENT_WORDS = 30  # fewer words → skip without LLM call


class ChatStore:
    """Thin SQLite wrapper for chat sessions and messages."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()
        log.info("ChatStore initialised at %s", self._path)

    def _bootstrap(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id  TEXT PRIMARY KEY,
                created_at  TEXT NOT NULL,
                title       TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL REFERENCES chat_sessions(session_id),
                role        TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                tokens_est  INTEGER,
                model_used  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_chat_msg_session
                ON chat_messages(session_id, created_at);
            """
        )
        self._conn.commit()

    # ── session management ────────────────────────────────────────────────────

    def create_session(self, title: str | None = None) -> str:
        sid = str(uuid.uuid4())
        ts = _now_iso()
        self._conn.execute(
            "INSERT INTO chat_sessions(session_id, created_at, title) VALUES (?,?,?)",
            (sid, ts, title or f"Session {ts[:10]}"),
        )
        self._conn.commit()
        return sid

    def list_sessions(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM chat_sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def ensure_session(self, session_id: str | None, title: str | None = None) -> str:
        """Return session_id, creating one if it doesn't exist."""
        if session_id:
            exists = self._conn.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if exists:
                return session_id
        return self.create_session(title=title)

    # ── message CRUD ─────────────────────────────────────────────────────────

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model_used: str | None = None,
    ) -> int:
        words = len(content.split())
        tokens_est = max(1, words * 4 // 3)
        cur = self._conn.execute(
            """INSERT INTO chat_messages(session_id, role, content, tokens_est, model_used)
               VALUES (?,?,?,?,?)""",
            (session_id, role, content, tokens_est, model_used),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_history(self, session_id: str, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            """SELECT role, content, created_at, model_used
               FROM chat_messages
               WHERE session_id = ?
               ORDER BY created_at ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


# ── InsightExtractor ─────────────────────────────────────────────────────────


class InsightExtractor:
    """Two-stage insight pipeline for ChromaDB ingestion.

    Stage 1 — Heuristic filter (free, instant):
      • Length gate   : content must have ≥ MIN_CONTENT_WORDS words
      • Keyword gate  : must contain ≥ 1 financial keyword
      • Noise reject  : skip if starts with greeting/filler phrases

    Stage 2 — LLM extraction (only if stage 1 passes):
      • Uses the cheapest available model (Groq llama3) via litellm
      • Prompt: extract 2-3 sentence factual financial insight or return SKIP

    Stage 3 — ChromaDB embed (only if stage 2 returns non-SKIP):
      • Collection: "assistant_insights"
      • Metadata: {ticker, session_id, timestamp, source, model_used}
    """

    def __init__(self, chroma_client: Any | None = None) -> None:
        self._chroma = chroma_client
        self._collection: Any | None = None
        if chroma_client is not None:
            try:
                self._collection = chroma_client.get_or_create_collection(
                    "assistant_insights"
                )
                log.info("InsightExtractor: ChromaDB collection ready")
            except Exception as exc:  # noqa: BLE001
                log.warning("InsightExtractor: ChromaDB unavailable — %s", exc)

    def _passes_heuristic(self, content: str) -> bool:
        """Fast, zero-cost pre-filter."""
        words = content.lower().split()
        if len(words) < _MIN_CONTENT_WORDS:
            return False
        # Noise prefix check
        first_80 = content[:80].lower().strip()
        if any(first_80.startswith(p) for p in _NOISE_PREFIXES):
            return False
        # Financial keyword gate
        content_lower = content.lower()
        return any(kw in content_lower for kw in _FINANCIAL_KEYWORDS)

    def _extract_via_llm(self, user_msg: str, ai_msg: str) -> str | None:
        """Call cheapest LLM to extract a 2-3 sentence insight. Returns None on SKIP/error."""
        try:
            import litellm
            from app.llm_router import provider_chain

            chain = provider_chain()
            if not chain:
                return None

            prompt = (
                "Extract a 2-3 sentence factual financial insight from the assistant's "
                "response below that would be valuable to embed in a knowledge base. "
                "If the response contains no concrete financial insight (e.g. it is "
                "conversational only), reply with exactly: SKIP\n\n"
                f"USER ASKED: {user_msg[:300]}\n\n"
                f"ASSISTANT RESPONDED: {ai_msg[:1000]}"
            )

            for model, kwargs in chain:
                try:
                    resp = litellm.completion(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=200,
                        temperature=0.1,
                        **{k: v for k, v in kwargs.items() if k != "extra_headers"},
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text.upper() == "SKIP" or not text:
                        return None
                    return text
                except Exception as exc:  # noqa: BLE001
                    log.debug("InsightExtractor LLM %s failed: %s", model, exc)
                    continue
        except Exception as exc:  # noqa: BLE001
            log.warning("InsightExtractor._extract_via_llm error: %s", exc)
        return None

    def maybe_embed(
        self,
        session_id: str,
        user_msg: str,
        ai_msg: str,
        ticker: str | None = None,
        model_used: str | None = None,
    ) -> bool:
        """Run the full pipeline. Returns True if an insight was embedded."""
        if not self._collection:
            return False

        if not self._passes_heuristic(ai_msg):
            log.debug("InsightExtractor: heuristic rejected (session=%s)", session_id)
            return False

        insight = self._extract_via_llm(user_msg, ai_msg)
        if not insight:
            log.debug("InsightExtractor: LLM returned SKIP (session=%s)", session_id)
            return False

        doc_id = f"chat-{session_id}-{int(time.time())}"
        try:
            self._collection.add(
                ids=[doc_id],
                documents=[insight],
                metadatas=[
                    {
                        "ticker": ticker or "GENERAL",
                        "session_id": session_id,
                        "timestamp": _now_iso(),
                        "source": "chat",
                        "model_used": model_used or "unknown",
                    }
                ],
            )
            log.info(
                "InsightExtractor: embedded insight (session=%s ticker=%s id=%s)",
                session_id,
                ticker,
                doc_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("InsightExtractor: ChromaDB add failed — %s", exc)
            return False


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
