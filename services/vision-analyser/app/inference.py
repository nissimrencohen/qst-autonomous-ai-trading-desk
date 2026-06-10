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


def _clip01(v: float) -> float:
    return round(min(1.0, max(0.0, v)), 4)


@lru_cache(maxsize=1)
def get_analyser() -> ChartAnalyser:
    if settings.model_backend == "torch":
        return TorchChartAnalyser(settings.model_path or None)
    return HeuristicChartAnalyser()
