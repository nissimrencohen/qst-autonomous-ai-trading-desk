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

### Option A — compose-managed n8n
1. Uncomment the `n8n` block in the root `docker-compose.yml` and
   `docker compose up -d`.
2. Open `http://localhost:5678`, create the local owner account.
3. *Workflows → Import from file* → `workflows/analyze-request.json`.
4. **Activate** the workflow. The production URL becomes
   `http://localhost:5678/webhook/analyze`.

### Option B — pre-existing standalone n8n container (headless, no UI)
This is the deployed setup. The workflow's HTTP nodes use compose-network
DNS names, so the standalone container must join the network first:

```bash
docker network connect trading-desk_trading-desk n8n

docker cp orchestration/n8n/workflows/analyze-request.json n8n:/tmp/wf.json
docker exec n8n n8n import:workflow --input=/tmp/wf.json
docker exec n8n n8n publish:workflow --id=TradingDeskWf001
docker restart n8n
python scripts/smoke_webhook.py     # verifies happy + blocked paths
```

### n8n 2.x gotchas (learned the hard way, 2026-06-10)
- **CLI import requires a top-level `"id"`** in the workflow JSON
  (`SQLITE_CONSTRAINT: workflow_entity.id` otherwise). Ours is pinned to
  `TradingDeskWf001`, which also makes re-imports idempotent upserts.
- **The Webhook node must carry a node-level `webhookId`**, or activation
  succeeds but the production webhook is never registered (404
  "webhook not registered").
- **`update:workflow --active=true` is deprecated and insufficient** — it
  activates an *empty published snapshot* (HTTP 200 with body `[]`, no
  execution recorded). Use `publish:workflow`, then restart n8n.
- **`$('Node')` references throw on skipped branches** ("Node ... hasn't
  been executed"). Any Code node reading optional branches (extraction,
  vision) must wrap the lookup in try/catch.

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
