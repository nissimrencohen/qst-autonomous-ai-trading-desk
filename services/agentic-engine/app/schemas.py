"""API schemas for the Agentic Engine.

`ProbabilityReport` is the system's primary deliverable — the structured JSON
contract consumed by the Guardrails output rail and the React dashboard.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

RiskLevel = Literal["low", "medium", "high"]


# ---- inputs (upstream service outputs, forwarded by n8n) -------------------

class RetrievedDocIn(BaseModel):
    id: str
    title: str
    source: str
    published_at: str
    text: str


class RagInput(BaseModel):
    summary: str | None = None
    retrieved: list[RetrievedDocIn] = []


class VisionInput(BaseModel):
    score: float = Field(ge=-1.0, le=1.0)
    label: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    patterns: dict[str, float] = {}


class SynthesizeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12, examples=["NVDA"])
    question: str = Field(min_length=3, max_length=500)
    horizon_days: int = Field(default=30, ge=1, le=365)
    rag: RagInput
    vision: VisionInput | None = None


# ---- output ----------------------------------------------------------------

class Probabilities(BaseModel):
    bullish: float = Field(ge=0.0, le=1.0)
    neutral: float = Field(ge=0.0, le=1.0)
    bearish: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sums_to_one(self) -> "Probabilities":
        total = self.bullish + self.neutral + self.bearish
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"probabilities must sum to 1.0, got {total:.3f}")
        return self


class TechnicalView(BaseModel):
    condition_score: float = Field(ge=-1.0, le=1.0)
    dominant_patterns: list[str] = []
    rationale: str


class FundamentalView(BaseModel):
    key_drivers: list[str] = []
    rationale: str
    sources: list[str] = []


class RiskAssessment(BaseModel):
    risk_level: RiskLevel
    key_risks: list[str] = []
    max_position_pct: float = Field(ge=0.0, le=100.0, description="Suggested cap, % of portfolio")
    notes: str = ""


class ProbabilityReport(BaseModel):
    run_id: str
    ticker: str
    question: str
    horizon_days: int
    generated_at: str
    probabilities: Probabilities
    technical_view: TechnicalView
    fundamental_view: FundamentalView
    risk_assessment: RiskAssessment
    confidence: float = Field(ge=0.0, le=1.0)
    caveats: list[str] = Field(
        min_length=1, description="Always non-empty — reports must carry uncertainty caveats"
    )
    engine_backend: str


class RunTrace(BaseModel):
    run_id: str
    ticker: str
    started_at: str
    finished_at: str | None
    steps: list[dict]
