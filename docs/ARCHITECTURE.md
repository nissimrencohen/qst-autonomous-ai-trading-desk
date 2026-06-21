# Architecture — Autonomous AI Trading Desk & Market Prediction Engine

> **Status:** Living document. Updated at the end of every verified execution step.
> **Last updated:** 2026-06-17 (v1.3 "Live Forecast Desk" — Phases 1–5 complete)

## 1. System Overview

The system evaluates fundamental and technical market data to generate trading
probability reports. A user submits an analysis request (ticker + optional chart
screenshot) through the UI; n8n dispatches the request to the agentic engine's
async orchestrator, which runs the full chain (guardrails → RAG → vision →
six-agent crew → output rail → GBM forecast) in a background thread and returns
a `run_id` immediately. The dashboard polls `GET /runs/{id}` until the report is
ready. Live market data (`/market-live`) populates the Command Center with
VIX-regime signals, index prices, and per-ticker momentum reads; regime changes
fire in-app alerts.

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — FRONTEND                                                 │
│  ┌──────────────────────────┐   ┌────────────────────────────────┐  │
│  │ React Trading Dashboard  │   │ Streamlit Admin Panel          │  │
│  │ real-time analysis,      │   │ raw data submission            │  │
│  │ charts, agent logs       │   │ (reports, news, chart images)  │  │
│  └────────────┬─────────────┘   └───────────────┬────────────────┘  │
└───────────────┼─────────────────────────────────┼───────────────────┘
                │ webhook (JSON)                  │ webhook (multipart)
┌───────────────▼─────────────────────────────────▼───────────────────┐
│  LAYER 2 — ORCHESTRATOR (n8n, API Gateway)                          │
│  payload validation → input guardrails → parallel routing →        │
│  synthesis → output guardrails → response                          │
└───────┬───────────────┬───────────────┬───────────────┬─────────────┘
        │               │               │               │
┌───────▼─────┐ ┌───────▼─────┐ ┌───────▼─────┐ ┌───────▼─────┐
│ RAG Service │ │ Vision      │ │ Agentic     │ │ Guardrails  │  LAYER 3
│ :8001       │ │ Analyser    │ │ Engine      │ │ Service     │  (FastAPI,
│ ChromaDB +  │ │ :8002       │ │ :8003       │ │ :8004       │  Docker,
│ HF embed    │ │ PyTorch     │ │ CrewAI      │ │ NeMo        │  AWS EC2)
└───────┬─────┘ │ ResNet-50/  │ │ multi-agent │ │ Guardrails  │
        │       │ EfficientNet│ └───────┬─────┘ └───────┬─────┘
        │       └─────────────┘         │               │
┌───────▼────────────────────────────────▼───────────────▼────────────┐
│  LAYER 4 — LLM LAYER                                                │
│  AWS Bedrock (primary)  ·  local Llama.cpp / Ollama (local tasks)   │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Layer Specifications

### Layer 1 — Frontend
| Component | Stack | Purpose |
|---|---|---|
| Trading Dashboard | React (Vite + TypeScript) | Main UI: submit analysis requests, display probability reports, live charts, streaming agent logs |
| Admin Panel | Streamlit | Internal tool: submit raw data (financial reports, news articles, chart screenshots) into the RAG/vision pipelines |

### Layer 2 — Orchestrator (n8n)
- Receives webhooks from both frontends.
- Performs initial payload validation (schema, required fields, file type checks).
- Calls the Guardrails Service for semantic input validation.
- Routes valid requests to the RAG Service and Vision Analyser **in parallel**.
- Forwards combined outputs to the Agentic Engine for synthesis.
- Passes the final report through the Guardrails Service output rail before responding.
- Workflow JSON exports are versioned under [orchestration/n8n/workflows](../orchestration/n8n/workflows).

### Layer 3 — Microservices (Python FastAPI · Docker · AWS EC2)

All services follow a shared convention:
- FastAPI app with `GET /health` (liveness) and `GET /ready` (dependency check).
- One Dockerfile per service (`python:3.11-slim` base unless GPU is required).
- Configuration via environment variables only (12-factor); no secrets in images.
- Structured JSON logging to stdout.

| Service | Port | Core stack | Responsibility |
|---|---|---|---|
| **RAG Service** | 8001 | ChromaDB, HuggingFace sentence-transformers, Bedrock / Llama.cpp summarizer | Ingests and retrieves historical financial reports & news; returns retrieved context + LLM summary |
| **Vision Analyser** | 8002 | PyTorch (ResNet-50 / EfficientNet) | Scores technical chart screenshots: support/resistance, breakouts → bullish/bearish condition score |
| **Agentic Engine** | 8003 | CrewAI (LangGraph-requirement equivalent), Bedrock | Multi-agent team — Technical Analyst, Fundamental Analyst, Risk Manager — synthesizes RAG + Vision outputs into structured JSON probability report |
| **Guardrails Service** | 8004 | NeMo Guardrails (YAML/Colang) | Input rails: block off-topic / illegal-asset requests. Output rails: block absolute financial guarantees & hallucinated metrics |

#### Endpoints (implemented)

```
RAG Service        POST /ingest            add document(s) to the vector store
                   POST /query             ticker-filtered top-k retrieval + LLM summary
                   GET  /market-live       live VIX curve, index prices, ticker signals (90 s TTL cache)
                   POST /updater/trigger   manual RAG updater cycle

Vision Analyser    POST /analyse           multipart {ticker, chart} → condition score JSON

Agentic Engine     POST /analyze           async orchestration entry → {run_id} (< 30 ms)
                   GET  /runs/{run_id}     trace + status + report (poll until status=done)
                   POST /synthesize        (legacy) direct synthesis (still available)
                   GET  /memory/{ticker}   per-ticker persisted analysis history (agent_memory.db)

Guardrails         POST /validate/input    {question, ticker} → allow/deny + violations
                   POST /validate/output   {text, evidence} → pass | sanitize | block

All services       GET  /health (liveness), GET /ready (dependency probes, 503 on failure)
```

#### Backend matrix (env-switchable, per 12-factor config)

| Service | Production backend | Dev/CI & degraded fallback |
|---|---|---|
| RAG store | `chroma` (persistent, HF `all-MiniLM-L6-v2`) | `memory` (keyword overlap) |
| RAG summarizer | `bedrock` / `ollama` | `extractive` (deterministic) |
| Vision | `torch` (ChartConditionNet, ResNet-50) | `heuristic` (ink-centroid trend) |
| Agentic | `crew` (CrewAI on Bedrock) | `deterministic` (rule-based) |
| Guardrails | `nemo` (rules + LLM self-check) | `rules` (deterministic rails only) |

Every fallback honors the same API contract, so the system is demoable and
testable offline end-to-end (verified by `scripts/e2e_local.py`).

### Layer 4 — LLM Layer
- **AWS Bedrock** — primary provider for agent reasoning and RAG summarization.
- **Llama.cpp / Ollama** — local models for latency-sensitive or offline-capable
  tasks (e.g., lightweight UI-side summarization).
- All model calls go through a thin provider-abstraction module per service so
  Bedrock ↔ local can be swapped via config.

## 3. Data Flow (v1.3 async run/poll — happy path)

1. User submits `{ticker, question, horizon_days, interval, chart_base64?, macro_context?}` from the React dashboard.
2. Dashboard POSTs to the n8n webhook (`/webhook/analyze`). n8n validates the payload (schema + Ollama ticker extractor if free-text) and calls Guardrails `/validate/input`. Blocked requests return `{blocked, reasons}` immediately.
3. n8n POSTs to **`POST /analyze`** (agentic-engine) and returns `{run_id}` to the browser in ~0.1–0.2 s.
4. Agentic-engine runs the full chain in a **background worker thread** (`asyncio.to_thread`):
   - Guardrails `/validate/input` (second check, degrades open on outage)
   - RAG `/query` → ticker-filtered retrieval + grounded summary
   - Vision `/analyse` → condition score (skipped if no chart)
   - Social pipeline → Reddit/Telegram sentiment for the ticker
   - Six-agent CrewAI crew → Technical, Fundamental, Volatility, Options Flow, Space Economy, News/Geo analysts run concurrently; Quant Execution Manager synthesises
   - Guardrails `/validate/output` → output rail (appends caveat on sanitize)
   - `build_forecast()` → GBM p10/p50/p90 projection (drift tilted by crew directional bias)
   - Result stored on `RunStore`
5. Dashboard polls `GET /runs/{run_id}` every 1.5 s; maps trace steps to pipeline stage indicator; resolves on `status==="done"`.
6. Report (including `forecast`) rendered; persisted to `localStorage` for reload.

**Macro cross-referencing (Phase 5):** An optional `macro_context` string (VIX regime, recommended exposure, hedging advice) can be injected at step 2 to condition all tickers in a batch under one shared market reading. The crew's Quant Execution Manager scales `max_position_pct` to the regime's recommended exposure.

**Market live feed:** `GET /market-live` (RAG-service, 90 s TTL cache) powers the Command Center and the ticker tape. VIX regime and market-heat changes between polling cycles fire `AlertEntry` events → persisted alert log + dismissable toast.

**Degraded mode:** If n8n is unreachable the dashboard falls back to direct `POST /analyze` on the agentic engine (same async run/poll pattern, no webhook hop).

Workflow export: [orchestration/n8n/workflows/analyze-request.json](../orchestration/n8n/workflows/analyze-request.json) · setup: [orchestration/n8n/README.md](../orchestration/n8n/README.md)

## 4. Docker & Deployment

- Each service ships its own `Dockerfile`; the root `docker-compose.yml`
  wires all four services + n8n (:5678) + the React dashboard (:3002, nginx)
  on a shared bridge network. The legacy Streamlit admin panel was retired in
  the final polish pass — manual document ingestion now lives in the
  dashboard's admin-only **INGEST** tab.
- Production target: AWS EC2 running Docker, one host initially; images pulled
  from a registry. GPU instance only required if Vision training/inference moves
  off CPU.
- Healthchecks in compose/ECS map to each service's `GET /health`.

## 5. Repository Layout

```
/docs                     project documentation (this file, changelog, todo, prompt log)
/services/rag-service     FastAPI + ChromaDB retrieval service
/services/vision-analyser FastAPI + PyTorch chart scoring service
/services/agentic-engine  FastAPI + CrewAI multi-agent service
/services/guardrails-service  FastAPI + NeMo Guardrails service
/frontend/trading-dashboard   React app (LIVE DESK, BRIEFING, ANALYSIS, admin-only INGEST)
/orchestration/n8n        n8n workflow JSON exports
/infra                    docker-compose, deployment scripts, IaC
/data/seed                mock/seed data (Nvidia, Elbit, Next Vision, Tondo Smart, CUE)
/scripts                  dev utilities
/tests                    cross-service integration tests
```

## 6. Domain Conventions

Mock data, tests, and seeds use realistic options-trading scenarios on these
tickers: **Nvidia (NVDA)**, **Elbit Systems (ESLT)**, **Next Vision (NXSN.TA)**,
**Tondo Smart (TOND.TA)**, **CUE (CUE)**.
