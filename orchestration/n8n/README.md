# n8n Orchestration

The orchestrator layer is a single n8n workflow:
[`workflows/analyze-request.json`](workflows/analyze-request.json).

## Flow

```
Webhook POST /webhook/analyze
  → Validate Payload (Code: shape/type checks → 400 on failure)
  → Needs Extraction? — if no ticker was supplied, an Ollama extractor
    (Family 1 prompt, see docs/PROMPT_ENGINEERING_LOG.md) pulls
    {ticker, horizon_days} from the free-text question
  → Guardrails /validate/input — off-topic & illegal-intent rail
      ├─ blocked → respond {blocked, stage: "input_rail", reasons}
      └─ allowed ↓ (parallel fan-out)
  → RAG /query  ∥  Vision /analyse (only when a chart was uploaded)
  → Merge → Build Synthesis Payload
  → Agentic Engine /synthesize → ProbabilityReport
  → Guardrails /validate/output (report prose vs. retrieved evidence)
      ├─ block    → respond {blocked, stage: "output_rail", reasons}
      ├─ sanitize → caveat appended, report passes
      └─ pass     → respond ProbabilityReport (+ output_rail verdict)
```

## Setup

1. `docker compose up -d` (repo root) — starts n8n on `:5678` plus all four
   services on the same bridge network (service DNS names are used in the
   workflow's HTTP nodes).
2. Open `http://localhost:5678`, create the local owner account.
3. *Workflows → Import from file* → `workflows/analyze-request.json`.
4. **Activate** the workflow. The production URL becomes
   `http://localhost:5678/webhook/analyze`.
5. Optional: set `OLLAMA_URL` / `OLLAMA_MODEL` env vars on the n8n container
   to enable free-text extraction (defaults target
   `host.docker.internal:11434`, model `llama3.1:8b`).

## Smoke test

```bash
curl -s -X POST http://localhost:5678/webhook/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","question":"Probability of upside into June expiry?","horizon_days":30}'
```

Export any workflow edits back into this folder (`Workflows → Download`) so
the orchestration layer stays version-controlled.
