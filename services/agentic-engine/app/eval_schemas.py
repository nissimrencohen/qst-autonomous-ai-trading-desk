"""EVAL Research Lab schemas — dynamic swarm & model configuration.

These models extend the production `SynthesizeRequest` contract with
two EVAL-specific dimensions:

  SwarmSize  — controls how many CrewAI agents participate in the synthesis:
                SOLO  (1): Quant Manager only (acts as a single-agent baseline)
                TRIAD (3): Technical + Fundamental + Manager (minimal viable desk)
                FULL  (7): Full production desk (current default, unchanged)

  EvalConfig — the per-request experiment configuration injected into every
               Langfuse trace, Phoenix eval score, and OTel span so the
               aggregation pipeline can group results by (experiment, config).

  EvalSynthesizeRequest — a drop-in superset of SynthesizeRequest that carries
                          an EvalConfig alongside the standard synthesis payload.
                          Accepted by POST /eval/synthesize.

These schemas are intentionally separate from `schemas.py` to avoid polluting
the production contract — `SynthesizeRequest` and `ProbabilityReport` are
unchanged and remain the canonical types consumed by the dashboard and n8n.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.schemas import SynthesizeRequest


class SwarmSize(str, Enum):
    """Controls how many CrewAI agents participate in a synthesis run.

    Used by the EVAL Research Lab to benchmark swarm size vs. quality/cost.

    SOLO  — Quant Manager only.  The manager gets full tool access (finance +
            search) so it can gather data independently.  This is the absolute
            baseline: one LLM call drives the whole report.

    TRIAD — Technical Analyst + Fundamental Analyst + Quant Manager.  The
            minimal productive desk: two specialist analysts run concurrently
            then the manager synthesises.  3× LLM calls per report (approx).

    FULL  — All seven production agents (Technical, Fundamental, Volatility,
            Options-Flow, Space Economy, News/Geopolitical, Quant Manager).
            This is the unchanged current production configuration.
    """

    SOLO = "solo"
    TRIAD = "triad"
    FULL = "full"


class EvalConfig(BaseModel):
    """Per-request EVAL configuration injected into every observability signal.

    Every Langfuse trace, Phoenix score, and OTel span produced by an eval
    run will carry these fields so the Phase 3 aggregation pipeline can group
    and compare results by (experiment_name, run_label, swarm_size, target_model).

    Attributes:
        experiment_name:
            Logical experiment grouping, e.g. "swarm_size_vs_model_impact".
            Used as a Langfuse tag and Phoenix metadata key.

        run_label:
            Human-readable configuration label within the experiment, e.g.
            "config_A_gpt4o_solo" or "triad_gemini_flash". Must be unique
            within the experiment to avoid result collisions in the dashboard.

        swarm_size:
            Number of agents to deploy. Defaults to FULL (production behaviour).

        target_model:
            LiteLLM model string to pin for this run, e.g.:
              "gpt-4o"
              "gemini/gemini-2.5-flash"
              "groq/llama-3.3-70b-versatile"
            When None the existing cascade (Groq → OpenAI → Gemini → Ollama)
            is used unchanged — safe for production calls that happen to carry
            an EvalConfig with default settings.

        skip_fallback:
            When True (default for EVAL) the pinned target_model is used
            WITHOUT a resilient fallback chain. This enforces benchmark
            integrity: a run that silently swapped providers would produce
            invalid comparison data.  Set to False only when you deliberately
            want cascade behaviour on top of the pinned primary.
    """

    experiment_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        examples=["swarm_size_vs_model_impact"],
        description="Logical experiment grouping for the Langfuse / Phoenix tag.",
    )
    run_label: str = Field(
        ...,
        min_length=1,
        max_length=128,
        examples=["config_A_gpt4o_solo"],
        description="Unique configuration label within the experiment.",
    )
    swarm_size: SwarmSize = Field(
        default=SwarmSize.FULL,
        description="Number of CrewAI agents to deploy for this run.",
    )
    target_model: str | None = Field(
        default=None,
        examples=["gpt-4o", "gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"],
        description=(
            "LiteLLM model string to pin. None → use existing cascade. "
            "Must include the provider prefix where applicable (e.g. 'groq/', 'gemini/')."
        ),
    )
    skip_fallback: bool = Field(
        default=True,
        description=(
            "When True and target_model is set, no resilient fallback chain "
            "is attached. The run fails explicitly if the pinned model is "
            "unavailable. Recommended True for EVAL benchmark integrity."
        ),
    )

    @property
    def langfuse_tags(self) -> list[str]:
        """Ordered tag list for Langfuse trace tagging."""
        tags = ["eval", self.experiment_name, self.run_label, self.swarm_size.value]
        if self.target_model:
            # Sanitise the model string for use as a tag (colons/slashes → dash)
            safe_model = self.target_model.replace("/", "-").replace(":", "-")
            tags.append(f"model-{safe_model}")
        return tags

    @property
    def metadata_dict(self) -> dict:
        """Flat metadata dict for Langfuse trace metadata and Phoenix scores."""
        return {
            "eval_experiment": self.experiment_name,
            "eval_run_label": self.run_label,
            "eval_swarm_size": self.swarm_size.value,
            "eval_target_model": self.target_model or "cascade",
            "eval_skip_fallback": self.skip_fallback,
        }


class EvalSynthesizeRequest(SynthesizeRequest):
    """Superset of SynthesizeRequest carrying an EvalConfig.

    Accepted by POST /eval/synthesize.  Inherits all SynthesizeRequest fields
    (including the watchlist validator on `ticker`) so EVAL runs go through the
    same input-validation path as production runs.

    The EvalConfig is stripped before forwarding to the underlying engine —
    it is purely metadata for the observability layer.
    """

    eval_config: EvalConfig = Field(
        ...,
        description="EVAL experiment configuration (swarm size + target model).",
    )
