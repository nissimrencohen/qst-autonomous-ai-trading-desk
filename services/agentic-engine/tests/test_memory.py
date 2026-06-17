"""Conversational memory unit tests — all backends, no external dependencies."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.memory import (
    _InProcessMemory,
    _SqliteMemory,
    format_prior_context,
)

_TURN = {
    "timestamp": "2026-06-17T10:00:00+00:00",
    "ticker": "NVDA",
    "question": "What is the probability of upside into June expiry?",
    "horizon_days": 30,
    "bull_prob": 0.42,
    "risk_level": "medium",
    "key_drivers": ["Data-center revenue up 38%", "IV elevated at 52%"],
    "engine_backend": "deterministic",
}


# ── in-process memory ──────────────────────────────────────────────────────────

def test_in_process_save_and_load():
    mem = _InProcessMemory()
    mem.save("NVDA", _TURN)
    turns = mem.load("NVDA")
    assert len(turns) == 1
    assert turns[0]["question"] == _TURN["question"]


def test_in_process_ticker_isolation():
    mem = _InProcessMemory()
    mem.save("NVDA", _TURN)
    assert mem.load("ESLT") == []


def test_in_process_ticker_case_normalised():
    mem = _InProcessMemory()
    mem.save("nvda", _TURN)
    assert len(mem.load("NVDA")) == 1


def test_in_process_caps_at_max_history():
    mem = _InProcessMemory()
    for i in range(6):
        mem.save("NVDA", {**_TURN, "question": f"q{i}"})
    turns = mem.load("NVDA")
    # _MAX_HISTORY == 3
    assert len(turns) == 3
    # most recent 3 are returned (oldest first)
    assert turns[-1]["question"] == "q5"


def test_in_process_empty_ticker_returns_empty():
    mem = _InProcessMemory()
    assert mem.load("TOND") == []


# ── SQLite memory ──────────────────────────────────────────────────────────────

def test_sqlite_save_and_load():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        mem = _SqliteMemory(db_path)
        mem.save("NVDA", _TURN)
        turns = mem.load("NVDA")
        assert len(turns) == 1
        assert turns[0]["bull_prob"] == pytest.approx(0.42)
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_sqlite_persists_across_instances():
    """A second _SqliteMemory instance on the same file can read what the first wrote."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _SqliteMemory(db_path).save("ESLT", {**_TURN, "ticker": "ESLT"})
        turns = _SqliteMemory(db_path).load("ESLT")
        assert len(turns) == 1
        assert turns[0]["ticker"] == "ESLT"
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_sqlite_ticker_isolation():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        mem = _SqliteMemory(db_path)
        mem.save("NVDA", _TURN)
        assert mem.load("CUE") == []
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_sqlite_caps_at_max_history():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        mem = _SqliteMemory(db_path)
        for i in range(5):
            mem.save("NVDA", {**_TURN, "question": f"q{i}"})
        turns = mem.load("NVDA")
        assert len(turns) == 3
        assert turns[-1]["question"] == "q4"
    finally:
        Path(db_path).unlink(missing_ok=True)


# ── format_prior_context ───────────────────────────────────────────────────────

def test_format_prior_context_empty():
    text = format_prior_context([])
    assert "No prior analysis" in text


def test_format_prior_context_formats_turns():
    text = format_prior_context([_TURN])
    assert "2026-06-17" in text
    assert "42%" in text
    assert "medium" in text
    assert "Data-center" in text


def test_format_prior_context_limits_drivers_to_three():
    turn = {**_TURN, "key_drivers": ["A", "B", "C", "D", "E"]}
    text = format_prior_context([turn])
    assert "D" not in text  # fourth driver is not included
