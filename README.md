# Autonomous AI Trading Desk & Market Prediction Engine

Production-grade system that evaluates fundamental and technical market data to
generate trading probability reports, built as an AI Engineering final project.

## Architecture (4 layers)

1. **Frontend** — React trading dashboard (`:3002`)
2. **Orchestrator** — n8n (webhook gateway: validation → guardrails → parallel RAG/Vision → synthesis → output-rail). Concurrent batch runs (up to 10 tickers) are driven by the Agentic Engine itself.
3. **Microservices** — 4 × Python FastAPI on AWS EC2 + Docker:
   RAG (ChromaDB), Vision Analyser (multimodal LLM), Agentic Engine (CrewAI, 7 agents), Guardrails (deterministic regex + NeMo)
4. **LLM Layer** — LiteLLM cascade with automatic failover:
   `gemini-2.5-flash → gemini-3.5-flash → groq/llama-3.3-70b → GitHub/OpenAI gpt-4o`
   Routed through Helicone for caching + cost tracking.

Full stack is 11 services (the 4 core microservices + dashboard + n8n + Langfuse,
Phoenix, Jaeger/OTel observability), wired via Docker Compose profiles.

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
frontend/        trading-dashboard (React)
orchestration/   n8n workflow exports
infra/           docker-compose, deployment
data/seed/       mock market data (NVDA, ESLT, NXSN, TOND, CUE)
tests/           cross-service integration tests
```

## Quick start

```bash
# core 4 services + dashboard:
docker compose up -d --build
# full stack incl. n8n + observability:
docker compose --profile n8n --profile langfuse --profile phoenix --profile observability up -d --build
# import orchestration/n8n/workflows/analyze-request.json into n8n (:5678), activate
python scripts/seed_rag.py          # seed the corpus
# dashboard: http://localhost:3002  (manual ingestion lives in the INGEST tab, admin-only)
```

Cloud deploy (AWS EC2, boto3 + paramiko IaC, migrates the named data volumes):
`python deploy/deploy_qst_aws.py`

Offline verification without Docker: `python scripts/e2e_local.py`
(boots all four services on dev backends and replays the full chain).

## Status

**Deployed on AWS EC2** — full 11-service stack live via Docker Compose profiles
(boto3/paramiko IaC, 50 GB gp3, hardened security group, internal tools reached
over an SSH tunnel). The LLM cascade fails over Gemini → Groq → GPT-4o on rate
limits/overload; multimodal chart vision runs on gemini-2.5-flash; output rail
grounds report metrics against the MCP server and flags hallucinations.
See [docs/TODO_AND_METRICS.md](docs/TODO_AND_METRICS.md) and [docs/CHANGELOG.md](docs/CHANGELOG.md).
