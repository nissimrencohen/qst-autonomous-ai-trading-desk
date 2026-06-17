"""Synthesis engines.

`CrewEngine` is the production backend: a CrewAI crew of three agents
(Technical Analyst, Fundamental Analyst, Risk Manager) driven by the
LLM router (Groq → Gemini → OpenAI → Ollama; Bedrock exclusively when
AGENTIC_ENVIRONMENT=aws). `DeterministicEngine` is a rule-based fallback
for dev/CI and degraded mode — same contract, no LLM calls. Selected via
`AGENTIC_ENGINE_BACKEND`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Protocol

from app.config import settings
from app.memory import build_memory, format_prior_context
from app.prompts import (
    FUNDAMENTAL_ANALYST,
    RISK_MANAGER,
    SYNTHESIS_TASK,
    TECHNICAL_ANALYST,
)
from app.runs import RunHandle
from app.schemas import (
    FundamentalView,
    Probabilities,
    ProbabilityReport,
    RiskAssessment,
    SynthesizeRequest,
    TechnicalView,
)

# \w* after the phase number covers sub-phases like "Phase 1b"
_BINARY_CATALYST_TERMS = re.compile(
    r"\b(phase\s*[123]\w*|trial|readout|binary|tender|fda|approval)\b", re.IGNORECASE
)


class SynthesisEngine(Protocol):
    name: str

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DeterministicEngine:
    """Transparent rule-based synthesis.

    The vision condition score (weighted by its confidence) tilts the
    probability mass between bullish and bearish; fundamentals contribute
    coverage/risk signals. Every rule is logged to the run trace so the
    dashboard can show the reasoning chain even without an LLM.
    """

    name = "deterministic"
    _TILT_WEIGHT = 0.35

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport:
        # --- technical leg ---
        if req.vision is not None:
            tilt = req.vision.score * req.vision.confidence
            dominant = sorted(
                (p for p in req.vision.patterns.items() if p[1] >= 0.5),
                key=lambda p: p[1],
                reverse=True,
            )[:2]
            technical = TechnicalView(
                condition_score=req.vision.score,
                dominant_patterns=[name for name, _ in dominant],
                rationale=(
                    f"Vision backend labeled the chart '{req.vision.label}' "
                    f"(score {req.vision.score:+.2f}, confidence {req.vision.confidence:.2f})."
                ),
            )
        else:
            tilt = 0.0
            technical = TechnicalView(
                condition_score=0.0,
                dominant_patterns=[],
                rationale="No chart was provided; no technical signal available.",
            )
        run.log("technical_analysis", {"tilt": round(tilt, 4)})

        # --- fundamental leg ---
        summary = req.rag.summary or ""
        drivers = [
            re.sub(r"\s*\[source:[^\]]*\]", "", line[2:]).strip()
            for line in summary.splitlines()
            if line.startswith("- ")
        ][:4]
        sources = [d.title for d in req.rag.retrieved]
        covered = bool(summary) and "does not cover" not in summary
        fundamental = FundamentalView(
            key_drivers=drivers,
            rationale=(
                f"RAG briefing over {len(req.rag.retrieved)} document(s)."
                if covered
                else "Retrieved context does not cover the question."
            ),
            sources=sources,
        )
        run.log("fundamental_analysis", {"covered": covered, "sources": len(sources)})

        # --- risk synthesis ---
        bullish = round(min(0.9, max(0.05, 1 / 3 + self._TILT_WEIGHT * tilt)), 4)
        bearish = round(min(0.9, max(0.05, 1 / 3 - self._TILT_WEIGHT * tilt)), 4)
        neutral = round(1.0 - bullish - bearish, 4)
        probs = Probabilities(bullish=bullish, neutral=neutral, bearish=bearish)

        binary_catalyst = bool(_BINARY_CATALYST_TERMS.search(summary))
        thin_coverage = len(req.rag.retrieved) < 2
        if binary_catalyst:
            risk_level, max_pos = "high", 2.0
        elif thin_coverage or req.vision is None:
            risk_level, max_pos = "medium", 3.0
        else:
            risk_level, max_pos = "low", 5.0

        key_risks = []
        if binary_catalyst:
            key_risks.append("Binary event risk detected in retrieved context.")
        if thin_coverage:
            key_risks.append("Fundamental coverage is thin (fewer than 2 documents).")
        if req.vision is None:
            key_risks.append("No technical confirmation available.")

        caveats = ["Probabilities are model-derived estimates, not assurances."]
        if not covered:
            caveats.append("Retrieved context did not cover the analyst question.")
        if req.vision is not None and req.vision.confidence < 0.4:
            caveats.append("Technical signal confidence is low.")

        confidence = round(
            0.3
            + 0.4 * (req.vision.confidence if req.vision else 0.0)
            + 0.2 * (not thin_coverage),
            4,
        )
        run.log(
            "risk_synthesis",
            {"risk_level": risk_level, "binary_catalyst": binary_catalyst},
        )

        return ProbabilityReport(
            run_id=run.run_id,
            ticker=req.ticker.upper(),
            question=req.question,
            horizon_days=req.horizon_days,
            generated_at=_now(),
            probabilities=probs,
            technical_view=technical,
            fundamental_view=fundamental,
            risk_assessment=RiskAssessment(
                risk_level=risk_level,
                key_risks=key_risks,
                max_position_pct=max_pos,
                notes=f"Deterministic policy: max position {int(max_pos)} pct.",
            ),
            confidence=min(confidence, 1.0),
            caveats=caveats,
            engine_backend=self.name,
        )


class CrewEngine:
    """CrewAI crew driven by the LLM router (Groq/Gemini/OpenAI/Ollama/Bedrock)."""

    name = "crew"

    def __init__(self) -> None:
        from crewai import Agent
        from app.llm_router import pick_crewai_llm
        from app.web_tools import build_search_tools

        self._llm = pick_crewai_llm()
        self._memory = build_memory()
        search_tools = build_search_tools()
        self._agents = {
            "technical": Agent(
                llm=self._llm, verbose=False, allow_delegation=False, **TECHNICAL_ANALYST
            ),
            "fundamental": Agent(
                llm=self._llm,
                verbose=False,
                allow_delegation=False,
                tools=search_tools,
                **FUNDAMENTAL_ANALYST,
            ),
            "risk": Agent(
                llm=self._llm, verbose=False, allow_delegation=False, **RISK_MANAGER
            ),
        }

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport:
        from crewai import Crew, Process, Task

        # load prior turns for this ticker so the Fundamental Analyst can
        # reference previous conclusions when RAG coverage is thin
        prior_turns = self._memory.load(req.ticker)
        prior_context = format_prior_context(prior_turns)
        run.log("memory_load", {"ticker": req.ticker.upper(), "turns": len(prior_turns)})

        inputs = {
            "ticker": req.ticker.upper(),
            "question": req.question,
            "horizon_days": req.horizon_days,
            "technical_thesis": json.dumps(
                req.vision.model_dump() if req.vision else {"signal": "unavailable"}
            ),
            "fundamental_thesis": req.rag.summary
            or "The retrieved context does not cover this.",
            "prior_context": prior_context,
        }

        technical_task = Task(
            description=(
                "Vision payload: {technical_thesis}\n"
                "Write the technical thesis for {ticker} ({horizon_days}d)."
            ),
            expected_output="3-5 sentence technical thesis with calibrated language.",
            agent=self._agents["technical"],
        )
        fundamental_task = Task(
            description=(
                "RAG briefing:\n{fundamental_thesis}\n\n"
                "Prior analysis history for {ticker}:\n{prior_context}\n\n"
                "List the key fundamental drivers for {ticker} relevant to: {question}"
            ),
            expected_output="2-4 drivers, each with [source: ...] or [web: <url>] attribution.",
            agent=self._agents["fundamental"],
        )
        synthesis_task = Task(
            description=SYNTHESIS_TASK,
            expected_output="A single JSON object matching the ProbabilityReport schema.",
            agent=self._agents["risk"],
            context=[technical_task, fundamental_task],
            output_pydantic=ProbabilityReport,
        )

        crew = Crew(
            agents=list(self._agents.values()),
            tasks=[technical_task, fundamental_task, synthesis_task],
            process=Process.sequential,
            verbose=False,
        )
        run.log("crew_kickoff", {"model": str(self._llm.model)})
        result = crew.kickoff(inputs=inputs)
        for task_output in result.tasks_output:
            run.log("agent_output", {"agent": task_output.agent, "summary": task_output.summary})

        report: ProbabilityReport = result.pydantic
        # authoritative fields are set server-side regardless of model output
        report = report.model_copy(
            update={
                "run_id": run.run_id,
                "ticker": req.ticker.upper(),
                "question": req.question,
                "horizon_days": req.horizon_days,
                "generated_at": _now(),
                "engine_backend": self.name,
            }
        )

        # persist this turn so future runs can reference it
        self._memory.save(
            req.ticker,
            {
                "timestamp": report.generated_at,
                "ticker": report.ticker,
                "question": report.question,
                "horizon_days": report.horizon_days,
                "bull_prob": report.probabilities.bullish,
                "risk_level": report.risk_assessment.risk_level,
                "key_drivers": report.fundamental_view.key_drivers,
                "engine_backend": report.engine_backend,
            },
        )
        run.log("memory_save", {"ticker": report.ticker})
        return report


def build_engine() -> SynthesisEngine:
    if settings.engine_backend == "crew":
        return CrewEngine()
    return DeterministicEngine()
