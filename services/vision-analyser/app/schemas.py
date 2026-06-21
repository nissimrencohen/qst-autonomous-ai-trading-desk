"""API schemas for the Vision Analyser."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Label = Literal["bullish", "bearish", "neutral"]


class ConditionScoreResponse(BaseModel):
    """Bullish/bearish condition score for one technical chart screenshot."""

    ticker: str = Field(examples=["NVDA"])
    score: float = Field(ge=-1.0, le=1.0, description="-1 = strongly bearish, +1 = strongly bullish")
    label: Label
    confidence: float = Field(ge=0.0, le=1.0)
    patterns: dict[str, float] = Field(
        description="Per-pattern probabilities: support_bounce, resistance_rejection, "
        "breakout_up, breakdown, consolidation"
    )
    model_backend: str
    latency_ms: float

class DescribeResponse(BaseModel):
    """Detailed textual description of a chart or image."""
    
    description: str
    model_backend: str
    latency_ms: float
