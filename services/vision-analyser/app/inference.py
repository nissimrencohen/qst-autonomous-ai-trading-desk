"""Chart-analysis backends.

`TorchChartAnalyser` is the production backend (ResNet-50 transfer model,
see `app.model`). `HeuristicChartAnalyser` is a deterministic,
dependency-light backend used for local dev, CI, and as a degraded-mode
fallback when no model weights are mounted. Both return the same
`ChartAnalysis` contract, selected via `VISION_MODEL_BACKEND`.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
from PIL import Image

from app.config import settings

PATTERNS = (
    "support_bounce",
    "resistance_rejection",
    "breakout_up",
    "breakdown",
    "consolidation",
)


@dataclass(frozen=True)
class ChartAnalysis:
    score: float  # -1 bearish .. +1 bullish
    confidence: float  # 0 .. 1
    patterns: dict[str, float]
    backend: str


class ChartAnalyser(Protocol):
    def analyse(self, image_bytes: bytes) -> ChartAnalysis: ...


class HeuristicChartAnalyser:
    """Trend extraction from price-chart 'ink'.

    For every pixel column, takes the vertical centroid of dark pixels
    (candles/line plot), yielding a price-like series y(x) in [0, 1].
    The least-squares slope of that series drives the condition score;
    simple positional rules over the series drive pattern probabilities.
    Deterministic by construction — identical bytes give identical scores.
    """

    name = "heuristic"
    _INK_THRESHOLD = 128  # grayscale value below which a pixel counts as ink

    def analyse(self, image_bytes: bytes) -> ChartAnalysis:
        gray = np.asarray(
            Image.open(io.BytesIO(image_bytes)).convert("L"), dtype=np.uint8
        )
        h, w = gray.shape
        ink = gray < self._INK_THRESHOLD

        xs, ys = [], []
        for col in range(w):
            rows = np.flatnonzero(ink[:, col])
            if rows.size:
                xs.append(col / max(w - 1, 1))
                # invert rows so larger y means higher price
                ys.append(1.0 - rows.mean() / max(h - 1, 1))

        coverage = len(xs) / max(w, 1)
        if len(xs) < 8:  # blank/non-chart image — no signal
            return ChartAnalysis(0.0, 0.0, {p: 0.0 for p in PATTERNS}, self.name)

        x = np.asarray(xs)
        y = np.asarray(ys)
        slope = float(np.polyfit(x, y, 1)[0])
        score = float(np.tanh(3.0 * slope))

        last = y[x >= 0.9]
        prior = y[x < 0.9]
        spread = float(y.std())
        patterns = {
            "breakout_up": _clip01(float(last.mean() - prior.max()) * 10 + 0.5)
            if last.size and prior.size
            else 0.0,
            "breakdown": _clip01(float(prior.min() - last.mean()) * 10 + 0.5)
            if last.size and prior.size
            else 0.0,
            "support_bounce": _clip01(float(y[-1] - y.min()) * 2) * (score > 0),
            "resistance_rejection": _clip01(float(y.max() - y[-1]) * 2) * (score < 0),
            "consolidation": _clip01(1.0 - spread * 8),
        }
        return ChartAnalysis(score, _clip01(coverage * 1.5), patterns, self.name)


class TorchChartAnalyser:
    """Production backend: ChartConditionNet (ResNet-50) inference."""

    name = "torch"

    def __init__(self, weights_path: str | None) -> None:
        import torch

        from app.model import build_model, preprocess

        self._torch = torch
        self._preprocess = preprocess
        self._model = build_model(pretrained=weights_path is None)
        if weights_path:
            state = torch.load(weights_path, map_location="cpu", weights_only=True)
            self._model.load_state_dict(state)
        self._model.eval()

    def analyse(self, image_bytes: bytes) -> ChartAnalysis:
        torch = self._torch
        batch = self._preprocess(image_bytes).unsqueeze(0)
        with torch.inference_mode():
            raw_score, pattern_logits = self._model(batch)
        probs = torch.sigmoid(pattern_logits)[0]
        return ChartAnalysis(
            score=float(torch.tanh(raw_score)[0]),
            confidence=float(probs.max()),
            patterns={p: round(float(v), 4) for p, v in zip(PATTERNS, probs)},
            backend=self.name,
        )


class LLMChartAnalyser:
    """Complexity-first multimodal LLM backend.

    Step 1 — gpt-4o-mini: fast and cheap; handles the majority of charts.
    Step 2 — gemini-2.5-flash: activated automatically when gpt-4o-mini
              returns confidence < VISION_LLM_ESCALATION_THRESHOLD (default 0.60).

    Both steps return the same ChartAnalysis contract.  If both fail (no API
    keys, quota exceeded) the analyser falls back to HeuristicChartAnalyser
    so chart analysis never hard-errors in production.
    """

    name = "llm"

    _SYSTEM_PROMPT = (
        "You are a quantitative technical analyst specialising in price charts. "
        "Analyse the chart image and return ONLY a valid JSON object — no markdown, "
        "no explanation — matching exactly this schema:\n"
        '{"score": <float -1.0 to 1.0>, '
        '"confidence": <float 0.0 to 1.0>, '
        '"patterns": {'
        '"support_bounce": <float>, '
        '"resistance_rejection": <float>, '
        '"breakout_up": <float>, '
        '"breakdown": <float>, '
        '"consolidation": <float>}}\n'
        "score: -1.0 = strongly bearish, +1.0 = strongly bullish. "
        "confidence: your certainty about the score. "
        "Pattern values are probabilities 0-1 (need not sum to 1). "
        "Return ONLY the JSON object."
    )

    def analyse(self, image_bytes: bytes) -> ChartAnalysis:
        import base64
        import json as _json

        b64 = base64.b64encode(image_bytes).decode()
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": "Analyse this chart and return the JSON."},
                ],
            },
        ]

        # ── Step 1: primary model (fast/cheap) ────────────────────────────
        result = self._call_model(
            settings.llm_vision_primary_model,
            settings.openai_api_key,
            messages,
        )
        if result is not None and result.confidence >= settings.llm_vision_escalation_threshold:
            return result

        # ── Step 2: escalate to heavier multimodal model ──────────────────
        import logging
        _log = logging.getLogger(__name__)
        _log.info(
            "Vision LLM: confidence=%.2f < %.2f — escalating to %s",
            result.confidence if result else 0.0,
            settings.llm_vision_escalation_threshold,
            settings.llm_vision_escalation_model,
        )
        escalated = self._call_model(
            settings.llm_vision_escalation_model,
            settings.google_api_key,
            messages,
        )
        if escalated is not None:
            return escalated

        # ── Fallback: heuristic (never hard-error in production) ──────────
        if result is not None:
            return result  # low-confidence primary beats no result
        return HeuristicChartAnalyser().analyse(image_bytes)

    def _call_model(
        self,
        model: str,
        api_key: str,
        messages: list,
    ) -> ChartAnalysis | None:
        import json as _json
        import logging
        import re

        _log = logging.getLogger(__name__)
        try:
            import litellm

            # 2048 (not 512): gemini-2.5-flash is a "thinking" model whose
            # reasoning tokens count toward the output budget — at 512 the JSON
            # got truncated mid-object (no closing brace) and was silently
            # dropped to the heuristic. Give both the reasoning and the small
            # JSON room to complete.
            kwargs: dict = {"model": model, "messages": messages, "max_tokens": 2048}
            if api_key:
                kwargs["api_key"] = api_key

            resp = litellm.completion(**kwargs)
            raw = resp.choices[0].message.content or ""
            # Strip ```json … ``` fences some models wrap the object in.
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                _log.warning("LLM vision %s returned no JSON: %s", model, raw[:200])
                return None
            data = _json.loads(m.group())
            patterns = {p: _clip01(float(data.get("patterns", {}).get(p, 0.0))) for p in PATTERNS}
            return ChartAnalysis(
                score=max(-1.0, min(1.0, float(data.get("score", 0.0)))),
                confidence=_clip01(float(data.get("confidence", 0.5))),
                patterns=patterns,
                backend=f"llm:{model}",
            )
        except ImportError:
            _log.warning("litellm not installed; LLM vision unavailable")
            return None
        except Exception as exc:
            _log.warning("LLM vision %s failed: %s", model, exc)
            return None


def _clip01(v: float) -> float:
    return round(min(1.0, max(0.0, v)), 4)


@lru_cache(maxsize=1)
def get_analyser() -> ChartAnalyser:
    if settings.model_backend == "torch":
        return TorchChartAnalyser(settings.model_path or None)
    if settings.model_backend == "llm":
        return LLMChartAnalyser()
    return HeuristicChartAnalyser()

def describe_image(image_bytes: bytes) -> tuple[str, str]:
    """Ask the multimodal LLM to describe the image in detail."""
    import base64
    import logging
    try:
        import litellm
    except ImportError:
        return ("Multimodal LLM not available (litellm not installed).", "none")

    _log = logging.getLogger(__name__)
    b64 = base64.b64encode(image_bytes).decode()
    
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "Extract all text, data tables, and describe the financial charts in detail."},
            ],
        },
    ]

    model = settings.llm_vision_primary_model
    api_key = settings.openai_api_key

    # Optionally escalate to google model if openai key is missing
    if not api_key and settings.google_api_key:
        model = settings.llm_vision_escalation_model
        api_key = settings.google_api_key

    kwargs: dict = {"model": model, "messages": messages, "max_tokens": 1024}
    if api_key:
        kwargs["api_key"] = api_key

    try:
        resp = litellm.completion(**kwargs)
        raw = resp.choices[0].message.content or ""
        return (raw, f"llm:{model}")
    except Exception as exc:
        _log.warning("describe_image %s failed: %s", model, exc)
        return (f"Failed to describe image: {exc}", f"error:{model}")
