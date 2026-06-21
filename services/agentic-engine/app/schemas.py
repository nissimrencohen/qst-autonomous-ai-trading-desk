"""API schemas for the Agentic Engine.

`ProbabilityReport` is the system's primary deliverable — the structured JSON
contract consumed by the Guardrails output rail and the React dashboard.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.watchlist import assert_whitelisted

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
    # When True (VOLATILITY DESK mode in the UI), the Volatility Analyst leads
    # and the report must carry a volatility_view. Optional → backward compatible.
    volatility_desk: bool = False
    # Optional shared macro/VIX regime context injected by the orchestrator so a
    # batch of tickers can be cross-referenced against ONE market regime (Phase 5).
    macro_context: str | None = None
    # Social media signals for this ticker, pre-formatted by the pipeline.
    # Empty string when the pipeline is disabled or no recent signals exist.
    social_context: str = ""

    # Cached technical indicators (RSI/MACD/Bollinger meta) from the ingestion
    # store. Lets the DeterministicEngine derive a directional tilt when no chart
    # (vision) is present — i.e. the continuous/offline path (Bug #3). Ignored by
    # the CrewEngine, which pulls TA via get_technical_indicators.
    ta_signal: dict | None = None

    # Strict watchlist enforcement (mission Req 1): reject off-list tickers
    # before any synthesis work begins, and normalise to the canonical symbol.
    @field_validator("ticker")
    @classmethod
    def _enforce_watchlist(cls, v: str) -> str:
        return assert_whitelisted(v)


class AnalyzeRequest(BaseModel):
    """Entry payload for the async orchestrator (POST /analyze).

    Self-contained: the orchestrator runs guardrails → RAG → vision → synthesis
    server-side in a background job and returns a run_id immediately, so neither
    n8n nor the browser blocks on the (minutes-long) crew. The optional chart is
    forwarded base64-encoded, mirroring the n8n webhook payload.
    """
    ticker: str = Field(min_length=1, max_length=12, examples=["NVDA"])
    question: str = Field(min_length=3, max_length=500)
    horizon_days: int = Field(default=30, ge=1, le=365)
    volatility_desk: bool = False
    chart_base64: str | None = None
    chart_content_type: str | None = None
    macro_context: str | None = None
    # "1d" = daily bars (default); "5m" = intraday 5-minute bars (horizon_days=1)
    interval: str = "1d"

    # Strict watchlist enforcement (mission Req 1) — see SynthesizeRequest.
    @field_validator("ticker")
    @classmethod
    def _enforce_watchlist(cls, v: str) -> str:
        return assert_whitelisted(v)


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


# ---- v2 institutional extensions (all optional — backward compatible) ------

Side = Literal["long", "short", "flat"]


class ExecutionPlan(BaseModel):
    """Quant Execution Manager output — concrete, PAPER-ONLY trade parameters.

    These are model-derived levels for a simulated/paper order, never an
    instruction to trade real capital. The frontend gates execution behind an
    explicit 'PAPER' button and never connects to a live broker.
    """
    side: Side = "flat"
    order_type: Literal["market", "limit", "stop"] = "limit"
    entry: float | None = Field(default=None, ge=0.0, description="Suggested entry price")
    target: float | None = Field(default=None, ge=0.0, description="Profit target")
    stop_loss: float | None = Field(default=None, ge=0.0, description="Protective stop")
    risk_reward_ratio: float | None = Field(
        default=None, ge=0.0, description="(target-entry)/(entry-stop) for a long"
    )
    reference_price: float | None = Field(
        default=None, ge=0.0, description="Live quote the plan was anchored to"
    )
    rationale: str = ""
    paper_only: bool = True


class VolatilityView(BaseModel):
    """VIX / volatility-desk read. Populated when the Volatility Analyst runs."""
    vix_level: float | None = Field(default=None, ge=0.0)
    term_structure: Literal["contango", "backwardation", "flat", "unknown"] = "unknown"
    front_month: float | None = Field(default=None, ge=0.0)
    back_month: float | None = Field(default=None, ge=0.0)
    regime: Literal["calm", "elevated", "stress", "panic", "unknown"] = "unknown"
    signal: str = ""


class SpaceEconomyView(BaseModel):
    """Space-sector read (Starlink / launch cadence / contracts). SPCX & peers."""
    key_drivers: list[str] = []
    launch_cadence: str = ""
    rationale: str = ""
    sources: list[str] = []


class ForecastPoint(BaseModel):
    t: str  # ISO date (daily) or ISO datetime (intraday)
    close: float | None = None  # historical actual (None on the projection)
    p10: float | None = None
    p50: float | None = None  # median path
    p90: float | None = None


class Forecast(BaseModel):
    """Transparent GBM price projection, drift-tilted by the crew's directional
    bias. NOT a precision predictor — closed-form lognormal quantile bands whose
    width grows with horizon (honest uncertainty). See PROMPT_ENGINEERING_LOG /
    strict eval for its known limitations (no regime-switching, thin tails)."""
    ticker: str
    interval: str  # "1d" | "5m" | ...
    model: str
    anchor_price: float
    drift_annual: float
    vol_annual: float
    directional_bias: float  # bullish − bearish, in [-1, 1]
    history: list[ForecastPoint]
    projection: list[ForecastPoint]
    generated_at: str


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
    # v2 extensions — optional so legacy reports & the deterministic engine validate
    execution_plan: ExecutionPlan | None = None
    volatility_view: VolatilityView | None = None
    space_economy_view: SpaceEconomyView | None = None
    forecast: Forecast | None = None  # predictive chart data (Phase 3)
    # Chart-vision read of an uploaded chart (when one was provided). Surfaced so
    # the UI can show that the chart was actually analysed and what it concluded.
    vision: VisionInput | None = None


RunStatus = Literal["running", "done", "blocked", "error"]


class RunTrace(BaseModel):
    run_id: str
    ticker: str
    started_at: str
    finished_at: str | None
    steps: list[dict]
    # async run/poll lifecycle (Phase 1): the dashboard polls GET /runs/{id} and
    # reads these instead of blocking on the synthesis HTTP call.
    status: RunStatus = "running"
    report: ProbabilityReport | None = None
    error: str | None = None
    blocked_reasons: list[str] = []
