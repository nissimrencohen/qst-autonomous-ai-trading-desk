"""Guardrails backends.

`RuleBackend` runs only the deterministic rails (dev/CI and degraded mode).
`NemoBackend` layers NeMo Guardrails LLM self-checks (rails/ config, Colang
flows) on top of the same deterministic rails — rules always run first, so a
hard violation never reaches the LLM. Selected via `GUARDRAILS_BACKEND`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app import rules
from app.schemas import (
    ValidateInputResponse,
    ValidateOutputResponse,
    Violation,
)

log = logging.getLogger(__name__)

RAILS_DIR = Path(__file__).resolve().parents[1] / "rails"


class GuardrailsBackend(Protocol):
    name: str

    def validate_input(self, question: str, ticker: str | None) -> ValidateInputResponse: ...
    def validate_output(self, text: str, evidence: list[str]) -> ValidateOutputResponse: ...


def _output_response(
    violations: list[Violation], sanitized: str, original: str, backend: str
) -> ValidateOutputResponse:
    blocking = [v for v in violations if v.rule == "hallucinated_metric"]
    if blocking:
        return ValidateOutputResponse(
            allowed=False, action="block", violations=violations, backend=backend
        )
    if violations:  # guarantee language only — salvageable
        return ValidateOutputResponse(
            allowed=True,
            action="sanitize",
            violations=violations,
            sanitized_text=sanitized,
            backend=backend,
        )
    return ValidateOutputResponse(allowed=True, action="pass", violations=[], backend=backend)


class RuleBackend:
    """Deterministic rails only."""

    name = "rules"

    def validate_input(self, question: str, ticker: str | None) -> ValidateInputResponse:
        violations = rules.check_input(question, ticker)
        return ValidateInputResponse(
            allowed=not violations, violations=violations, backend=self.name
        )

    def validate_output(self, text: str, evidence: list[str]) -> ValidateOutputResponse:
        violations, sanitized = rules.check_output(text, evidence)
        return _output_response(violations, sanitized, text, self.name)


class NemoBackend:
    """Deterministic rails + NeMo Guardrails LLM self-checks."""

    name = "nemo"

    def __init__(self, rails_dir: Path | None = None) -> None:
        from nemoguardrails import LLMRails, RailsConfig

        config = RailsConfig.from_path(str(rails_dir or RAILS_DIR))
        self._rails = LLMRails(config)

    def validate_input(self, question: str, ticker: str | None) -> ValidateInputResponse:
        deterministic = rules.check_input(question, ticker)
        if deterministic:
            return ValidateInputResponse(
                allowed=False, violations=deterministic, backend=self.name
            )
        result = self._rails.generate(
            messages=[{"role": "user", "content": question}]
        )
        refused = "cannot assist" in result["content"].lower()
        violations = (
            [Violation(rule="llm_input_rail", detail=result["content"])] if refused else []
        )
        return ValidateInputResponse(
            allowed=not refused, violations=violations, backend=self.name
        )

    def validate_output(self, text: str, evidence: list[str]) -> ValidateOutputResponse:
        violations, sanitized = rules.check_output(text, evidence)
        response = _output_response(violations, sanitized, text, self.name)
        if not response.allowed:
            return response
        result = self._rails.generate(
            messages=[
                {"role": "context", "content": {"evidence": "\n".join(evidence)}},
                {"role": "user", "content": f"Validate this report text:\n{text}"},
            ]
        )
        if "cannot assist" in result["content"].lower():
            response.violations.append(
                Violation(rule="llm_output_rail", detail=result["content"])
            )
            return ValidateOutputResponse(
                allowed=False, action="block",
                violations=response.violations, backend=self.name,
            )
        return response


def build_backend() -> GuardrailsBackend:
    from app.config import settings

    if settings.backend == "nemo":
        return NemoBackend()
    return RuleBackend()
