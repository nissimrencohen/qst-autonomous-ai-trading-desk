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

## Status

**Step 1 of 7 complete** — repository + documentation initialized.
See [docs/TODO_AND_METRICS.md](docs/TODO_AND_METRICS.md) for live status.
