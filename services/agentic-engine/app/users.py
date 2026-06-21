"""User persistence + RBAC store — real DB-backed authentication.

A thin SQLite-backed `users` table (admin / standard roles) with bcrypt
password hashing via passlib.  Seeded on startup with a default admin and a
default standard user **only when the table is empty** — never overwrites
existing rows, so changing the seed env vars after first boot is a no-op.

Tables
──────
  users (id PK, username UNIQUE, hashed_password, role, created_at)

Roles
─────
  "admin" → full access incl. the INGEST tab / manual upload
  "user"  → LIVE DESK, BRIEFING, ANALYSIS MODE, ASSISTANT only
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, TypedDict

log = logging.getLogger(__name__)

Role = Literal["admin", "user"]

# We use the `bcrypt` library directly rather than passlib's CryptContext:
# passlib 1.7.x is incompatible with bcrypt >= 4.1 (its backend-detection probe
# raises on the 72-byte limit), which would break at import time. bcrypt direct
# is the modern, supported path and produces standard $2b$ hashes.
_BCRYPT_MAX_BYTES = 72  # bcrypt silently/loudly ignores bytes past this


def _encode(plain: str) -> bytes:
    """UTF-8 encode and truncate to bcrypt's 72-byte input limit."""
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    import bcrypt

    return bcrypt.hashpw(_encode(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        import bcrypt

        return bcrypt.checkpw(_encode(plain), hashed.encode("utf-8"))
    except Exception:  # malformed hash, missing backend, etc.
        return False


class UserRecord(TypedDict):
    id: int
    username: str
    hashed_password: str
    role: str
    created_at: str


class UserStore:
    """SQLite-backed user directory with bcrypt password hashing."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        # Ensure the parent dir exists (e.g. ./data on a fresh checkout).
        parent = Path(self._path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()
        log.info("UserStore initialised at %s", self._path)

    def _bootstrap(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL UNIQUE,
                hashed_password TEXT    NOT NULL,
                role            TEXT    NOT NULL CHECK(role IN ('admin','user')),
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            """
        )
        self._conn.commit()

    # ── reads ────────────────────────────────────────────────────────────────
    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM users")
        return int(cur.fetchone()["n"])

    def get(self, username: str) -> Optional[UserRecord]:
        cur = self._conn.execute(
            "SELECT id, username, hashed_password, role, created_at "
            "FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        return dict(row) if row else None  # type: ignore[return-value]

    # ── writes ─────────────────────────────────────────────────────────────────
    def create(self, username: str, password: str, role: Role) -> None:
        self._conn.execute(
            "INSERT INTO users (username, hashed_password, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    # ── auth ───────────────────────────────────────────────────────────────────
    def authenticate(self, username: str, password: str) -> Optional[UserRecord]:
        """Return the user record on a valid username+password, else None."""
        rec = self.get(username)
        if rec and verify_password(password, rec["hashed_password"]):
            return rec
        return None

    def seed_defaults(self, admin_password: str, user_password: str) -> bool:
        """Create a default admin + standard user ONLY when the table is empty.

        Returns True if seeding ran, False if users already existed.
        """
        if self.count() > 0:
            log.info("UserStore already populated (%d users) — skipping seed", self.count())
            return False
        self.create("admin", admin_password, "admin")
        self.create("user", user_password, "user")
        log.info("UserStore seeded default users: admin (admin), user (user)")
        return True
