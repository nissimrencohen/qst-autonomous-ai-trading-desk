"""Vision Analyser API routes."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import settings
from app.inference import get_analyser
from app.schemas import ConditionScoreResponse

log = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}


@router.post("/analyse", response_model=ConditionScoreResponse, tags=["analysis"])
async def analyse_chart(
    ticker: str = Form(min_length=1, max_length=12, examples=["NVDA"]),
    chart: UploadFile = File(description="Technical chart screenshot"),
) -> ConditionScoreResponse:
    """Score a technical chart screenshot as a bullish/bearish condition."""
    if chart.content_type not in _ALLOWED_TYPES:
        raise HTTPException(415, f"unsupported content type: {chart.content_type}")
    data = await chart.read()
    if len(data) > settings.max_image_bytes:
        raise HTTPException(413, "image exceeds size limit")
    if not data:
        raise HTTPException(422, "empty upload")

    t0 = time.perf_counter()
    try:
        analysis = get_analyser().analyse(data)
    except Exception:  # corrupt image, decode failure
        log.exception("analysis failed for ticker=%s", ticker)
        raise HTTPException(422, "could not decode chart image") from None
    latency_ms = (time.perf_counter() - t0) * 1000

    if analysis.score > settings.bullish_threshold:
        label = "bullish"
    elif analysis.score < -settings.bullish_threshold:
        label = "bearish"
    else:
        label = "neutral"

    log.info(
        "analysed ticker=%s score=%.3f label=%s backend=%s",
        ticker, analysis.score, label, analysis.backend,
    )
    return ConditionScoreResponse(
        ticker=ticker.upper(),
        score=round(analysis.score, 4),
        label=label,
        confidence=round(analysis.confidence, 4),
        patterns=analysis.patterns,
        model_backend=analysis.backend,
        latency_ms=round(latency_ms, 2),
    )
