"""Guardrails service API routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.schemas import (
    ValidateInputRequest,
    ValidateInputResponse,
    ValidateOutputRequest,
    ValidateOutputResponse,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/validate/input", response_model=ValidateInputResponse, tags=["rails"])
def validate_input(payload: ValidateInputRequest, request: Request) -> ValidateInputResponse:
    """Input rail: block off-topic and illegal-asset/illegal-intent requests."""
    result = request.app.state.guard.validate_input(payload.question, payload.ticker)
    if not result.allowed:
        log.warning(
            "input blocked source=%s rules=%s",
            payload.source, [v.rule for v in result.violations],
        )
    return result


@router.post("/validate/output", response_model=ValidateOutputResponse, tags=["rails"])
def validate_output(payload: ValidateOutputRequest, request: Request) -> ValidateOutputResponse:
    """Output rail: block hallucinated metrics, sanitize guarantee language."""
    result = request.app.state.guard.validate_output(payload.text, payload.evidence)
    if result.action != "pass":
        log.warning(
            "output action=%s rules=%s",
            result.action, [v.rule for v in result.violations],
        )
    return result
