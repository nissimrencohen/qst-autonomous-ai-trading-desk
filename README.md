# Autonomous AI Trading Desk & Market Prediction Engine

Production-grade system that evaluates fundamental and technical market data to
generate trading probability reports, built as an AI Engineering final project.

## Architecture (4 layers)

1. **Frontend** — React trading dashboard + Streamlit admin panel
2. **Orchestrator** — n8n (webhook gateway, validation, parallel routing)
3. **Microservices** — 4 × Python FastAPI on AWS EC2 + Docker:
   RAG (ChromaDB), Vision Analyser (PyTorch), Agentic Engine (CrewAI), Guardrails (NeMo)
4. **LLM Layer** — AWS Bedrock (primary) + local Llama.cpp/Ollama

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Documentation

| File | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, endpoints, data flow, Docker conventions |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Chronological log of features, refactors, fixes |
| [docs/TODO_AND_METRICS.md](docs/TODO_AND_METRICS.md) | Requirement checklist & project metrics |
| [docs/PROMPT_ENGINEERING_LOG.md](docs/PROMPT_ENGINEERING_LOG.md) | **Graded artifact:** ≥5 iterations per system prompt + 10-case pass rates |

## Repository layout

```
services/        rag-service · vision-analyser · agentic-engine · guardrails-service
frontend/        trading-dashboard (React) · admin-panel (Streamlit)
orchestration/   n8n workflow exports
infra/           docker-compose, deployment
data/seed/       mock market data (NVDA, ESLT, NXSN, TOND, CUE)
tests/           cross-service integration tests
```

## Quick start

```bash
docker compose up -d --build        # 4 services + n8n + dashboard + admin
# import orchestration/n8n/workflows/analyze-request.json into n8n (:5678), activate
python scripts/seed_rag.py          # seed the corpus (NVDA/ESLT/NXSN/TOND/CUE)
# dashboard: http://localhost:3000 · admin: http://localhost:8501
```

Offline verification without Docker: `python scripts/e2e_local.py`
(boots all four services on dev backends and replays the full chain).

## Status

**All 7 steps complete (v1.0.0)** — system wired end-to-end and verified:
E2E chain 12/12 assertions at 1.5s latency; 46 unit/integration tests passing;
all 5 prompt families logged (25 iterations, pass rates ≥ 9/10).
Remaining: EC2 deployment. See [docs/TODO_AND_METRICS.md](docs/TODO_AND_METRICS.md).
