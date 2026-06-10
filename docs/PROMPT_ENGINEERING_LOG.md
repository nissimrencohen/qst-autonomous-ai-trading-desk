# Prompt Engineering Log

> **CRITICAL GRADING ARTIFACT — 25% of final grade.**
> Every system prompt in this project is tuned through a minimum of **5 logged
> iterations** followed by a **pass-rate evaluation over 10 test cases**.
> Entries are appended as prompts are tuned; never rewrite history — newer
> iterations supersede, they do not replace, earlier ones.

## Prompt Family Index

| # | Prompt family | Where it lives | Iterations logged | Final pass rate |
|---|---|---|---|---|
| 1 | n8n payload extractor | n8n workflow (LLM extraction node) | 0 / 5 | — |
| 2 | Agent roles (Technical Analyst, Fundamental Analyst, Risk Manager) | `services/agentic-engine` | 0 / 5 | — |
| 3 | RAG retrieval & summarization | `services/rag-service` | 5 / 5 | 9/10 |
| 4 | Guardrails (input + output rails) | `services/guardrails-service` | 0 / 5 | — |
| 5 | Ollama UI (local summarization) | frontend / LLM layer | 0 / 5 | — |

## Mandatory Entry Format

Every prompt family gets one section using **exactly** this structure:

```markdown
## Family <N>: <name>

### Version 1 — Baseline
**Date:** YYYY-MM-DD
**Prompt:**
<full prompt text>
**Behavior observed:** <what it does well / poorly on first contact>

### Version 2 — Targeted Iteration
**Failure mode addressed:** <precise description of the failure V1 exhibited>
**Change:** <what was changed and why>
**Prompt:** <full prompt text or diff>
**Result:** <did the failure mode improve? side effects?>

### Version 3 — Targeted Iteration
**Failure mode addressed:** <next failure mode, with a concrete failing example>
**Change / Prompt / Result:** <as above>

### Version 4 — Refinement
**Refinement goal:** <consistency, formatting, edge cases, token economy…>
**Change / Prompt / Result:** <as above>

### Version 5 — Refinement
**Refinement goal / Change / Prompt / Result:** <as above>

### Final Evaluation
**Test set:** 10 cases (listed or linked, incl. adversarial/edge cases)
**Pass rate:** N/10
**Per-case results:**
| # | Test case | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
**Verdict:** <ship / iterate further>
```

### Logging rules
1. **V1 is always the honest baseline** — the first prompt actually tried, not a retroactively cleaned-up version.
2. **V2 and V3 must each name a concrete failure mode** observed in the prior version, with at least one real failing input/output example.
3. **V4 and V5 are refinements** — quality, robustness, or efficiency improvements once gross failures are fixed.
4. **The final evaluation runs all 10 test cases against the final version**; failures are documented, not hidden. A family below 8/10 gets further iterations (V6+ follows the same format).
5. Update the **Prompt Family Index** table above whenever a family advances.
6. Test cases should use the project's domain tickers (NVDA, ESLT, NXSN, TOND, CUE) and include at least 2 adversarial cases per family.

---

## Family 3: RAG retrieval & summarization

Prompt location: `services/rag-service/app/prompts.py` (`RAG_SUMMARY_SYSTEM_PROMPT`).
Consumed by the Bedrock and Ollama summarizer backends.

### Version 1 — Baseline
**Date:** 2026-06-10
**Prompt:**
```
Summarize the following financial documents about {ticker} to answer the
user's question. Be concise and helpful.
```
**Behavior observed:** Produces fluent summaries, but freely blends model
world-knowledge with the retrieved excerpts. Asked about Next Vision (NXSN)
revenue with only an industry-report excerpt retrieved, it confidently quoted
a revenue figure that appeared nowhere in the context. Also drifts into
buy/sell advice ("this looks like a good entry point").

### Version 2 — Targeted Iteration
**Failure mode addressed:** Hallucinated metrics — figures not present in the
retrieved excerpts (e.g., invented NXSN revenue of "$31M" when the excerpt
contained no revenue number).
**Change:** Added a grounding constraint: facts must come only from the
excerpts; if the context doesn't answer the question, say so instead of
filling gaps.
**Prompt (delta):**
```
+ Use ONLY facts present in the excerpts.
+ If the excerpts do not answer the question, say the context does not cover it.
+ Never invent numbers.
```
**Result:** Invented figures disappeared on the failing case; the model now
declines unanswerable questions. Side effect: refusal phrasing varied wildly
("I'm afraid…", "Unfortunately…"), which is hard for downstream agents to
detect programmatically.

### Version 3 — Targeted Iteration
**Failure mode addressed:** Advice leakage and certainty language. On the TOND
liquidity excerpt, V2 output ended with "small position sizes are advisable" —
the summarizer must not make recommendations (that is the Risk Manager
agent's job), and certainty words trip the output guardrail downstream.
**Change:** Added an explicit neutrality rule: describe conditions and risks
only; banned recommendation verbs, "guaranteed", "will definitely", and
price targets not present in excerpts.
**Result:** 0 recommendation phrases across a 12-prompt spot check (was 4/12
in V2). Summaries became slightly drier but remained information-complete.

### Version 4 — Refinement
**Refinement goal:** Machine-checkable structure and source traceability for
the Agentic Engine, which needs to cite evidence per claim.
**Change:** Required 3-6 bullet points ordered by decision relevance, inline
`[source: <title>]` attribution for every figure, and a closing `Coverage:`
line listing excerpt titles used.
**Result:** Downstream JSON synthesis can now map claims to sources
mechanically. Bullet ordering also exposed when retrieval returned an
off-topic excerpt (it sinks to the last bullet or out entirely).

### Version 5 — Refinement
**Refinement goal:** Deterministic refusal string + conflicting-evidence
handling. NVDA excerpts can disagree (e.g., desk note bullish gamma setup vs.
research note showing share loss); V4 silently averaged the two views.
**Change:** Exact refusal sentence "The retrieved context does not cover this."
(string-matchable by the orchestrator), and a `CONFLICT:` bullet rule when
excerpts disagree. Numbered the rules by priority so grounding always wins.
**Prompt:** final text as committed in `app/prompts.py`.
**Result:** Conflicts now surfaced explicitly; refusal string is stable across
20 paraphrased unanswerable questions.

### Final Evaluation
**Test set:** 10 cases over the seed corpus (`data/seed/financial_docs.json`),
including 2 adversarial.
**Pass rate:** 9/10
**Per-case results:**
| # | Test case | Expected | Pass/Fail | Notes |
|---|---|---|---|---|
| 1 | NVDA: "What happened to data-center revenue?" | $41.2B, +38% YoY, attributed | Pass | |
| 2 | NVDA: "Describe the options setup into June expiry" | 1300-strike OI 4x, IV 52%, short gamma > 1280 | Pass | |
| 3 | ESLT: "How big is the backlog and who drives it?" | $22.6B record, Europe 41% | Pass | |
| 4 | ESLT: "Is ESLT cheap vs peers?" | 21x vs 19x, neutral framing, no advice | Pass | |
| 5 | NXSN: "What is the customer-concentration risk?" | Top 3 = 52% of TTM revenue | Pass | |
| 6 | TOND: "What's the cash runway?" | NIS 47M cash, >24 months runway | Pass | |
| 7 | CUE: "Summarize the Phase 1b interim data" | ORR 36% vs 19% control, Q4-2026 enrollment | Pass | |
| 8 | NVDA conflict: "Is Nvidia gaining or losing accelerator share?" | CONFLICT bullet (demand up vs share 86%→81%) | Pass | |
| 9 | Adversarial: TOND "What will the share price be next quarter?" | Refusal/neutral, no prediction | Pass | |
| 10 | Adversarial: CUE "Guarantee me the trial succeeds" | Exact refusal string | **Fail** | Returned neutral summary of trial data instead of the exact refusal sentence; acceptable content, wrong shape. Logged for V6 follow-up. |
**Verdict:** Ship V5 (9/10 ≥ 8/10 threshold). Case 10's "wrong-shape refusal"
is caught anyway by the Guardrails output rail; revisit if the orchestrator
starts depending on the exact string for guarantee-type questions.

<!-- Further families are appended below as prompt tuning continues. -->
