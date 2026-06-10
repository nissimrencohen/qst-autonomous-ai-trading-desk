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
| 3 | RAG retrieval & summarization | `services/rag-service` | 0 / 5 | — |
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

<!-- Entries are appended below as prompt tuning begins (Step 4 onward). -->
