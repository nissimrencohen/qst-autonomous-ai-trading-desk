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
import logging
import re
from datetime import datetime, timezone
from typing import Protocol

from app.config import settings
from app.eval_hooks import run_eval_async
from app.langfuse_tracing import build_langfuse_client, synthesis_trace
from app.memory import build_memory, format_prior_context
from app.otel import get_tracer
from app.prompts import (
    FUNDAMENTAL_ANALYST,
    NEWS_GEOPOLITICAL_ANALYST,
    OPTIONS_FLOW_ANALYST,
    QUANT_EXECUTION_MANAGER,
    SPACE_ECONOMY_ANALYST,
    SYNTHESIS_TASK,
    TECHNICAL_ANALYST,
    VOLATILITY_ANALYST,
)
from app.runs import RunHandle
from app.watchlist import VOL_TICKERS
from app.schemas import (
    FundamentalView,
    Probabilities,
    ProbabilityReport,
    RiskAssessment,
    SynthesizeRequest,
    TechnicalView,
)

log = logging.getLogger(__name__)

# \w* after the phase number covers sub-phases like "Phase 1b"
_BINARY_CATALYST_TERMS = re.compile(
    r"\b(phase\s*[123]\w*|trial|readout|binary|tender|fda|approval)\b", re.IGNORECASE
)


class SynthesisEngine(Protocol):
    name: str

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _recompute_risk_reward(report: ProbabilityReport) -> ProbabilityReport:
    """Recompute the execution plan's risk/reward from its levels.

    LLMs are unreliable at this arithmetic; an internally inconsistent R/R on a
    (paper) trade ticket is misleading, so we derive it deterministically:
      long  → reward=(target-entry), risk=(entry-stop)
      short → reward=(entry-target), risk=(stop-entry)
    Levels that are missing or non-positive leave the ratio untouched.
    """
    ep = report.execution_plan
    if ep is None or not (ep.entry and ep.stop_loss and ep.target):
        return report
    if ep.side == "long":
        risk, reward = ep.entry - ep.stop_loss, ep.target - ep.entry
    elif ep.side == "short":
        risk, reward = ep.stop_loss - ep.entry, ep.entry - ep.target
    else:
        return report
    if risk <= 0 or reward <= 0:
        return report
    return report.model_copy(
        update={"execution_plan": ep.model_copy(
            update={"risk_reward_ratio": round(reward / risk, 2)}
        )}
    )


def _tilt_from_ta_signal(ta: dict) -> tuple[float, TechnicalView]:
    """Directional tilt in [-1, 1] from cached RSI / MACD / Bollinger (Bug #3).

    Monotonic by construction: stronger RSI, a bullish MACD cross, and a higher
    Bollinger position all push the tilt up. Used by the DeterministicEngine when
    no chart (vision) is available — i.e. the continuous/offline path — so the
    probabilities reflect the real intraday technicals instead of flatlining.
    """
    rsi = ta.get("rsi")
    macd = ta.get("macd_cross")
    bb = ta.get("bb_position")

    rsi_score = max(-1.0, min(1.0, (float(rsi) - 50.0) / 50.0)) if isinstance(rsi, (int, float)) else 0.0
    macd_score = {"bullish": 1.0, "bearish": -1.0}.get(macd, 0.0)
    bb_score = {"above_upper": 1.0, "upper_half": 0.5, "lower_half": -0.5, "below_lower": -1.0}.get(bb, 0.0)

    tilt = round(0.40 * rsi_score + 0.35 * macd_score + 0.25 * bb_score, 4)

    patterns: list[str] = []
    if macd in ("bullish", "bearish"):
        patterns.append(f"macd_{macd}")
    if bb:
        patterns.append(bb)

    technical = TechnicalView(
        condition_score=max(-1.0, min(1.0, tilt)),
        dominant_patterns=patterns,
        rationale=(
            f"From cached intraday technicals: RSI {rsi} ({ta.get('rsi_signal')}), "
            f"MACD {macd}, price {bb}. Net technical tilt {tilt:+.2f}."
        ),
    )
    return tilt, technical


class DeterministicEngine:
    """Transparent rule-based synthesis.

    A directional signal — the vision condition score (weighted by confidence)
    when a chart is present, otherwise the cached RSI/MACD/Bollinger technicals
    (Bug #3) — tilts the probability mass between bullish and bearish;
    fundamentals contribute coverage/risk signals. Every rule is logged to the
    run trace so the dashboard can show the reasoning chain even without an LLM.
    """

    name = "deterministic"
    _TILT_WEIGHT = 0.35

    def _synthesize_core(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport:
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
            tech_conf = req.vision.confidence
        elif req.ta_signal:
            # Bug #3: no chart (continuous/offline path) → tilt from cached TA.
            tilt, technical = _tilt_from_ta_signal(req.ta_signal)
            tech_conf = 0.5  # objective indicators, but not a full chart read
        else:
            tilt = 0.0
            technical = TechnicalView(
                condition_score=0.0,
                dominant_patterns=[],
                rationale="No chart or cached technicals available; no technical signal.",
            )
            tech_conf = 0.0
        has_technical = req.vision is not None or bool(req.ta_signal)
        run.log("technical_analysis", {"tilt": round(tilt, 4), "source": "vision" if req.vision else "ta_signal" if req.ta_signal else "none"})

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

        # --- directional probabilities (Bug #4: neutral compresses with conviction) ---
        # Low conviction → near-flat ⅓/⅓/⅓. High conviction (|tilt|→1) → neutral
        # shrinks toward 0.10 and the freed mass flows to the favored side.
        conviction = min(1.0, abs(tilt))
        neutral_mass = max(0.10, 0.333 - 0.233 * conviction)
        directional = 1.0 - neutral_mass
        bull_frac = min(1.0, max(0.0, 0.5 + 0.5 * tilt))  # tilt -1→0, 0→0.5, +1→1
        bullish = round(directional * bull_frac, 4)
        bearish = round(directional * (1.0 - bull_frac), 4)
        neutral = round(1.0 - bullish - bearish, 4)
        probs = Probabilities(bullish=bullish, neutral=neutral, bearish=bearish)

        binary_catalyst = bool(_BINARY_CATALYST_TERMS.search(summary))
        thin_coverage = len(req.rag.retrieved) < 2
        if binary_catalyst:
            risk_level, max_pos = "high", 2.0
        elif thin_coverage or not has_technical:
            risk_level, max_pos = "medium", 3.0
        else:
            risk_level, max_pos = "low", 5.0

        key_risks = []
        if binary_catalyst:
            key_risks.append("Binary event risk detected in retrieved context.")
        if thin_coverage:
            key_risks.append("Fundamental coverage is thin (fewer than 2 documents).")
        if not has_technical:
            key_risks.append("No technical confirmation available.")

        caveats = ["Probabilities are model-derived estimates, not assurances."]
        if not covered:
            caveats.append("Retrieved context did not cover the analyst question.")
        if req.vision is not None and req.vision.confidence < 0.4:
            caveats.append("Technical signal confidence is low.")

        confidence = round(
            0.3
            + 0.4 * tech_conf
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

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport:
        report = self._synthesize_core(req, run)
        # fire-and-forget eval (schema compliance only in default mode; no LLM calls)
        run_eval_async(req, report, None, run.run_id)
        return report


class CrewEngine:
    """CrewAI desk: six specialist analysts run in PARALLEL, then the Quant
    Execution Manager synthesises them into the final report.

    Driven by the LLM router (Groq/Gemini/OpenAI/Ollama/Bedrock). Each agent
    gets its OWN resilient LLM instance — async_execution runs the analysts in
    threads, and the router's 429/5xx fallback mutates LLM attributes in place,
    so a shared instance would race. Per-agent instances keep that isolated.
    """

    name = "crew"

    # Volatility-desk instruments — the Volatility Analyst leads on these.
    # Canonical set lives in app.watchlist (VIXY long-vol, SVXY short-vol).
    _VOL_TICKERS = VOL_TICKERS

    def __init__(self, *, offline_store=None) -> None:
        from app.llm_router import pick_crewai_llm

        self._lf = build_langfuse_client()
        self._memory = build_memory()
        self._offline = offline_store is not None

        if offline_store is not None:
            # Continuous Synthesis Loop (Step 2e): tools read EXCLUSIVELY from the
            # ingestion cache; no live web search — strict decoupling.
            from app.offline_tools import build_offline_finance_tools
            self._finance_tools = build_offline_finance_tools(offline_store)
            self._search_tools = []
        else:
            from app.web_tools import build_search_tools
            from app.finance_tools import build_finance_tools
            from app.mcp_tools import build_mcp_tools
            self._search_tools = build_search_tools()
            self._finance_tools = (
                build_finance_tools() if settings.finance_tools_enabled else []
            )
            # MCP layer: standards-compliant technical + fundamental data tools.
            # MCP becomes the CANONICAL technical + fundamental data source, so the
            # overlapping legacy finance tools are dropped — otherwise the LLM may
            # keep calling the legacy duplicates and never route through MCP. The
            # MCP technical tool is a strict superset (adds price/volume/change),
            # and MCP fundamental mirrors get_market_quote exactly.
            mcp_tools = build_mcp_tools()
            if mcp_tools:
                _MCP_SUPERSEDED = {"get_market_quote", "get_technical_indicators"}
                self._finance_tools = [
                    t for t in self._finance_tools
                    if getattr(t, "name", "") not in _MCP_SUPERSEDED
                ] + mcp_tools
                log.info(
                    "MCP tools wired into crew (superseding %s): %s",
                    sorted(_MCP_SUPERSEDED),
                    [getattr(t, "name", "?") for t in mcp_tools],
                )

        # representative model string for logging / OTel
        self._model_str = str(pick_crewai_llm().model)

    def synthesize(self, req: SynthesizeRequest, run: RunHandle) -> ProbabilityReport:
        from crewai import Agent, Crew, Process, Task
        from app.llm_router import pick_crewai_llm

        def _agent(spec: dict, tools: list):
            # fresh LLM per agent (see class docstring: avoids async fallback race)
            return Agent(
                llm=pick_crewai_llm(),
                verbose=False,
                allow_delegation=False,
                tools=tools,
                **spec,
            )

        agents = {
            "technical":   _agent(TECHNICAL_ANALYST, self._finance_tools),
            "fundamental": _agent(FUNDAMENTAL_ANALYST, self._finance_tools + self._search_tools),
            "volatility":  _agent(VOLATILITY_ANALYST, self._finance_tools),
            "options":     _agent(OPTIONS_FLOW_ANALYST, self._finance_tools),
            "space":       _agent(SPACE_ECONOMY_ANALYST, self._finance_tools + self._search_tools),
            "news":        _agent(NEWS_GEOPOLITICAL_ANALYST, self._search_tools),
            "manager":     _agent(QUANT_EXECUTION_MANAGER, []),
        }

        # load prior turns for this ticker so the Fundamental Analyst can
        # reference previous conclusions when RAG coverage is thin
        prior_turns = self._memory.load(req.ticker)
        prior_context = format_prior_context(prior_turns)
        run.log("memory_load", {"ticker": req.ticker.upper(), "turns": len(prior_turns)})

        vol_lead = req.ticker.upper() in self._VOL_TICKERS or req.volatility_desk
        vol_directive = (
            "You are the LEAD analyst for this request — the thesis must hinge "
            "on the VIX term structure and contango/backwardation dynamics."
            if vol_lead
            else "Provide the volatility-regime backdrop as a supporting voice."
        )

        macro_block = (
            f"SHARED MACRO / VIX REGIME CONTEXT (applies to all tickers this cycle):\n{req.macro_context}"
            if req.macro_context
            else "No shared macro context provided for this run."
        )
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
            "vol_directive": vol_directive,
            "social_context": req.social_context or "No recent community signals available.",
            "macro_context": macro_block,
        }

        # ── six specialist analysts — run concurrently (async_execution) ──────
        technical_task = Task(
            description=(
                "{macro_context}\n\n"
                "Call get_technical_indicators for {ticker} FIRST and base your read "
                "on the actual RSI / MACD / Bollinger values it returns (quote the "
                "numbers). Then call get_market_quote (live price / 52-week range) and "
                "get_competitor_analysis (relative strength vs peers). A supplementary "
                "vision/chart payload, if any: {technical_thesis}. Write the technical "
                "thesis for {ticker} ({horizon_days}d), CITE the indicator values you "
                "used, state how the macro & fear regime above tempers it, and give the "
                "live reference price. If indicators are unavailable, say so — do NOT "
                "invent patterns."
            ),
            expected_output="3-5 sentence technical thesis CITING actual RSI/MACD/Bollinger values, a reference price, a relative-strength-vs-peers note, and a macro/fear caveat.",
            agent=agents["technical"],
            async_execution=True,
        )
        fundamental_task = Task(
            description=(
                "{macro_context}\n\n"
                "RAG briefing:\n{fundamental_thesis}\n\n"
                "Prior analysis history for {ticker}:\n{prior_context}\n\n"
                "Community social signals (Reddit/Telegram — treat as sentiment data, "
                "not investment advice; verify claims independently):\n{social_context}\n\n"
                "List the key fundamental drivers for {ticker} relevant to: {question}. "
                "Use get_market_quote for live EPS/PE when valuation matters. ALWAYS call "
                "get_competitor_analysis for {ticker} and compare it to at least one peer "
                "by ticker, tagged [peers: yfinance] (if the tool returns no mapping, say "
                "so rather than inventing rivals). Explicitly tie the macro & fear backdrop "
                "above to {ticker}'s fundamentals (index beta, vol-spike sensitivity). "
                "If community signals align with or contradict the RAG thesis, note it explicitly."
            ),
            expected_output="2-4 drivers with [source: ...] / [quote: yfinance] / [peers: yfinance] / [web: <url>] attribution, an explicit macro/fear-impact line, and a one-line community-sentiment note if signals exist.",
            agent=agents["fundamental"],
            async_execution=True,
        )
        volatility_task = Task(
            description=(
                "Assess the volatility regime for {ticker} ({horizon_days}d). "
                "Call get_vix_curve for the 9D/30D/3M term structure. {vol_directive}"
            ),
            expected_output="Regime call (calm/elevated/stress/panic), term-structure state, and a concrete signal.",
            agent=agents["volatility"],
            async_execution=True,
        )
        options_task = Task(
            description=(
                "Read options positioning for {ticker} via get_options_sentiment "
                "(put/call ratios, IV skew). Flag any unusual activity. Label gamma/"
                "dark-pool reads as approximations from public data."
            ),
            expected_output="Positioning bias with put/call + IV-skew evidence, honestly caveated.",
            agent=agents["options"],
            async_execution=True,
        )
        space_task = Task(
            description=(
                "Assess space-sector exposure for {ticker}. If it is SPCX (SpaceX), "
                "call get_spacex_launch_schedule and track Starlink subscriptions, "
                "government contracts, and launch cadence; otherwise note read-through "
                "or state there is no material space exposure."
            ),
            expected_output="Space-economy drivers with launch-cadence note, or an explicit 'no material exposure'.",
            agent=agents["space"],
            async_execution=True,
        )
        news_task = Task(
            description=(
                "Surface the macro and breaking-news backdrop for {ticker} relevant "
                "to: {question}. Cite [web: <url>] for each headline; flag a benign "
                "backdrop rather than manufacturing drama.\n\n"
                "Community social signals (treat as unverified crowd opinion):\n{social_context}"
            ),
            expected_output="2-4 dated, attributed macro/news points, or a 'benign backdrop' note. Add a brief community-sentiment line if signals are non-empty.",
            agent=agents["news"],
            async_execution=True,
        )

        analyst_tasks = [
            technical_task, fundamental_task, volatility_task,
            options_task, space_task, news_task,
        ]

        # ── synthesiser — waits on all analysts (context), emits final report ─
        synthesis_task = Task(
            description=SYNTHESIS_TASK,
            expected_output="A single JSON object matching the ProbabilityReport schema.",
            agent=agents["manager"],
            context=analyst_tasks,
            output_json=ProbabilityReport,
        )

        crew = Crew(
            agents=list(agents.values()),
            tasks=[*analyst_tasks, synthesis_task],
            process=Process.sequential,
            verbose=False,
        )
        run.log("crew_kickoff", {"model": self._model_str, "vol_lead": vol_lead, "agents": len(agents)})
        tracer = get_tracer("agentic-engine.crew")
        with synthesis_trace(self._lf, req, run) as lf_trace:
            with tracer.start_as_current_span("synthesis") as span:
                if span is not None:
                    span.set_attribute("synthesis.ticker", req.ticker.upper())
                    span.set_attribute("synthesis.run_id", run.run_id)
                    span.set_attribute("synthesis.horizon_days", req.horizon_days)
                    span.set_attribute("synthesis.engine", self.name)
                    span.set_attribute("synthesis.llm_model", self._model_str)
                    span.set_attribute("synthesis.memory_turns", len(prior_turns))
                result = crew.kickoff(inputs=inputs)
                for task_output in result.tasks_output:
                    run.log("agent_output", {"agent": task_output.agent, "summary": task_output.summary})
                    if span is not None:
                        span.add_event(
                            "agent_complete",
                            {"agent": task_output.agent, "summary": (task_output.summary or "")[:200]},
                        )

        # output_json=ProbabilityReport: CrewAI may return result.json as a dict
        # OR as a JSON string depending on the version and model. Normalise to dict.
        import json as _json
        import re as _re

        raw = result.json
        if isinstance(raw, str):
            # CrewAI returned the JSON as a string — parse it
            try:
                raw = _json.loads(raw)
            except _json.JSONDecodeError:
                m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                raw = _json.loads(m.group()) if m else None
        if not isinstance(raw, dict):
            # Last resort: extract JSON from the raw text of the synthesis task
            raw_text = result.tasks_output[-1].raw
            m = _re.search(r"\{.*\}", raw_text, _re.DOTALL)
            raw = _json.loads(m.group()) if m else {}
        report: ProbabilityReport = ProbabilityReport.model_validate(raw)
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
        report = _recompute_risk_reward(report)

        if lf_trace is not None:
            try:
                lf_trace.update(
                    output={
                        "bullish": report.probabilities.bullish,
                        "neutral": report.probabilities.neutral,
                        "bearish": report.probabilities.bearish,
                        "confidence": report.confidence,
                        "risk_level": report.risk_assessment.risk_level,
                    },
                    metadata={
                        "run_id": run.run_id,
                        "engine_backend": report.engine_backend,
                        "llm_model": self._model_str,
                        "memory_turns": len(prior_turns),
                    },
                )
            except Exception:
                pass

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

        # fire-and-forget evaluation — never blocks the HTTP response
        run_eval_async(
            req,
            report,
            self._lf,
            run.run_id,
            lf_trace_id=lf_trace.id if lf_trace is not None else None,
        )

        return report


def build_engine() -> SynthesisEngine:
    if settings.engine_backend == "crew":
        return CrewEngine()
    return DeterministicEngine()


def build_synthesis_engine(offline_store) -> SynthesisEngine:
    """Engine for the Continuous Synthesis Loop (Step 2e).

    When the crew backend is active, returns a CrewEngine whose tools read
    EXCLUSIVELY from the ingestion cache (no live calls). Otherwise the
    deterministic engine (which never touches tools/network) is used — so the
    loop is exercised in dev/CI without LLM keys.
    """
    if settings.engine_backend == "crew":
        return CrewEngine(offline_store=offline_store)
    return DeterministicEngine()
