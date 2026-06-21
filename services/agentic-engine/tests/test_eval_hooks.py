"""Unit tests for eval_hooks (Step 4).

All tests run without LLM calls:
  - schema_compliance is deterministic
  - run_eval_async with backend=none/schema is tested via mocking
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_report(**overrides):
    """Return a minimal valid ProbabilityReport-like namespace for tests."""
    from app.schemas import (
        FundamentalView,
        Probabilities,
        ProbabilityReport,
        RiskAssessment,
        TechnicalView,
    )

    base = dict(
        run_id="run-test-001",
        ticker="NVDA",
        question="What is the upside probability?",
        horizon_days=30,
        generated_at="2026-06-17T04:00:00+00:00",
        probabilities=Probabilities(bullish=0.50, neutral=0.30, bearish=0.20),
        technical_view=TechnicalView(
            condition_score=0.5,
            dominant_patterns=["breakout_up"],
            rationale="Strong breakout.",
        ),
        fundamental_view=FundamentalView(
            key_drivers=["Data-center revenue +38% YoY"],
            rationale="Solid fundamentals.",
            sources=["Nvidia Q1-2026 earnings"],
        ),
        risk_assessment=RiskAssessment(
            risk_level="low",
            key_risks=[],
            max_position_pct=5.0,
            notes="",
        ),
        confidence=0.72,
        caveats=["Probabilities are model-derived estimates, not assurances."],
        engine_backend="deterministic",
    )
    base.update(overrides)
    return ProbabilityReport(**base)


def _make_req(**overrides):
    """Return a minimal SynthesizeRequest."""
    from app.schemas import RagInput, RetrievedDocIn, SynthesizeRequest, VisionInput

    base = dict(
        ticker="NVDA",
        question="What is the upside probability?",
        horizon_days=30,
        rag=RagInput(
            summary="- Nvidia reported data-center revenue of $41.2B. [source: Q1 results]",
            retrieved=[
                RetrievedDocIn(
                    id="NVDA-1",
                    title="Nvidia Q1-2026 results",
                    source="earnings call",
                    published_at="2026-05-21",
                    text="Nvidia reported data-center revenue of $41.2B...",
                )
            ],
        ),
        vision=VisionInput(score=0.82, label="bullish", confidence=0.9, patterns={"breakout_up": 0.8}),
    )
    base.update(overrides)
    return SynthesizeRequest(**base)


# ── Schema compliance tests ───────────────────────────────────────────────────


def test_schema_compliance_pass() -> None:
    """A fully valid report should score 1.0."""
    from app.eval_hooks import _schema_compliance

    report = _make_report()
    assert _schema_compliance(report) == 1.0


def test_schema_compliance_fail_prob_sum() -> None:
    """Probabilities that don't sum to 1.0 should score 0.0."""
    from app.schemas import Probabilities

    from app.eval_hooks import _schema_compliance

    # Create report with probabilities that intentionally skip the validator
    report = _make_report()
    # Manually break the sum by monkey-patching the object
    bad_probs = MagicMock()
    bad_probs.bullish = 0.6
    bad_probs.neutral = 0.6
    bad_probs.bearish = 0.6
    report = _make_report()
    object.__setattr__(report, "probabilities", bad_probs)

    assert _schema_compliance(report) == 0.0


def test_schema_compliance_fail_no_caveats() -> None:
    """A report with empty caveats list should score 0.0."""
    from app.eval_hooks import _schema_compliance

    # Bypass pydantic min_length=1 constraint using model_copy
    report = _make_report()
    report = report.model_copy(update={"caveats": []})

    assert _schema_compliance(report) == 0.0


def test_schema_compliance_fail_invalid_risk_level() -> None:
    """An unknown risk_level should score 0.0."""
    from app.eval_hooks import _schema_compliance
    from app.schemas import RiskAssessment

    report = _make_report()
    bad_risk = MagicMock()
    bad_risk.risk_level = "extreme"  # not in {low, medium, high}
    bad_risk.key_risks = []
    bad_risk.max_position_pct = 5.0
    object.__setattr__(report, "risk_assessment", bad_risk)

    assert _schema_compliance(report) == 0.0


# ── run_eval_async integration tests ─────────────────────────────────────────


def test_run_eval_async_no_op_when_disabled() -> None:
    """When eval_backend=none, run_eval_async should return immediately
    without submitting anything to the executor."""
    from app.eval_hooks import run_eval_async

    req = _make_req()
    report = _make_report()

    with patch("app.eval_hooks._EXECUTOR") as mock_exec, patch(
        "app.config.settings"
    ) as mock_settings:
        mock_settings.eval_backend = "none"
        run_eval_async(req, report, None, "run-noop-001")
        mock_exec.submit.assert_not_called()


def test_run_eval_async_schema_only_posts_langfuse_score() -> None:
    """With eval_backend=schema, the worker should call lf_client.score()
    with schema_compliance score=1.0 for a valid report."""
    from app.eval_hooks import _eval_worker

    req = _make_req()
    report = _make_report()
    mock_lf = MagicMock()

    with patch("app.config.settings") as mock_settings:
        mock_settings.eval_backend = "schema"
        mock_settings.phoenix_endpoint = ""
        _eval_worker(req, report, mock_lf, "run-schema-001", "lf-trace-abc")

    # Langfuse score should have been called once (schema_compliance)
    mock_lf.score.assert_called_once()
    call_kwargs = mock_lf.score.call_args.kwargs
    assert call_kwargs["name"] == "schema_compliance"
    assert call_kwargs["value"] == 1.0
    assert call_kwargs["trace_id"] == "lf-trace-abc"
