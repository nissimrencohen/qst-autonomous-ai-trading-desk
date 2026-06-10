# Architecture — Autonomous AI Trading Desk & Market Prediction Engine

> **Status:** Living document. Updated at the end of every verified execution step.
> **Last updated:** 2026-06-10 (Step 7 — full system wired; all 7 steps complete)

## 1. System Overview

The system evaluates fundamental and technical market data to generate trading
probability reports. A user submits an analysis request (ticker + optional chart
screenshot) through the UI; the orchestrator validates and fans the request out to
parallel analysis services; an agentic engine synthesizes the results into a
structured probability report, which is validated by guardrails before being
returned to the dashboard.

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

#### Endpoints (implemented, Steps 2–5)

```
RAG Service        POST /ingest            add document(s) to the vector store
                   POST /query             ticker-filtered top-k retrieval + LLM summary

Vision Analyser    POST /analyse           multipart {ticker, chart} → condition score JSON

Agentic Engine     POST /synthesize        RAG + Vision payloads → ProbabilityReport
                   GET  /runs/{run_id}     agent execution trace (dashboard log panel)

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

## 3. Data Flow (happy path)

1. User submits `{ticker, question, horizon_days, chart_base64?}` from the React dashboard to `POST /webhook/analyze`.
2. n8n validates payload shape (Code node); malformed requests get 400 immediately. If no ticker was supplied, the **Ollama Extract** node (prompt Family 1) pulls `{ticker, horizon_days}` from the free text.
3. n8n → Guardrails `/validate/input`. Off-topic, insider, manipulation, or illicit-asset requests are denied with `{blocked, stage: "input_rail", reasons}`.
4. n8n fans out in parallel:
   - RAG `/query` → ticker-filtered retrieval + grounded summary.
   - Vision `/analyse` → condition score (branch skipped when no chart was uploaded).
5. n8n merges both legs → Agentic Engine `/synthesize`. The crew (Technical Analyst, Fundamental Analyst, Risk Manager) produces the validated `ProbabilityReport` JSON.
6. n8n → Guardrails `/validate/output` with the report prose + retrieved evidence. Verdicts: `pass` / `sanitize` (caveat appended) / `block`.
7. Validated report returns to the dashboard; the agent trace streams via `GET /runs/{run_id}`.

Degraded mode: the dashboard falls back to driving the services directly
(same chain, guardrails included) if the orchestrator is unreachable.

Workflow export: [orchestration/n8n/workflows/analyze-request.json](../orchestration/n8n/workflows/analyze-request.json) · setup: [orchestration/n8n/README.md](../orchestration/n8n/README.md)

## 4. Docker & Deployment

- Each service ships its own `Dockerfile`; the root `docker-compose.yml`
  wires all four services + n8n (:5678) + the React dashboard (:3000, nginx)
  + the Streamlit admin panel (:8501) on a shared bridge network.
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
/frontend/trading-dashboard   React app
/frontend/admin-panel     Streamlit app
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
