"""API schemas for the Guardrails service."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["pass", "sanitize", "block"]


class Violation(BaseModel):
    rule: str = Field(examples=["absolute_guarantee"])
    detail: str
    excerpt: str | None = None


class ValidateInputRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    ticker: str | None = Field(default=None, max_length=12)
    source: str = Field(default="dashboard", description="Origin UI, for audit logs")


class ValidateInputResponse(BaseModel):
    allowed: bool
    violations: list[Violation] = []
    backend: str


class ValidateOutputRequest(BaseModel):
    text: str = Field(min_length=1, description="Report prose to validate (rationales, summary)")
    evidence: list[str] = Field(
        default=[],
        description="Source texts the report was built from; claim-like figures "
        "missing from the evidence are flagged as hallucinated metrics",
    )


class ValidateOutputResponse(BaseModel):
    allowed: bool
    action: Action
    violations: list[Violation] = []
    sanitized_text: str | None = Field(
        default=None, description="Set when action == 'sanitize'"
    )
    backend: str
