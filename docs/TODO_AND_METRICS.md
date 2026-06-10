# TODO & Metrics — Requirement Checklist

> **Rule:** This file is updated after every successful, verified step.
> Legend: `[x]` done & verified · `[~]` in progress · `[ ]` not started

## Execution Plan Status

| Step | Scope | Status | Verified |
|---|---|---|---|
| 1 | Repo structure + `/docs` initialization | ✅ Done | 2026-06-10 |
| 2 | Scaffold 4 FastAPI services (health checks + Dockerfiles) | ✅ Done | 2026-06-10 |
| 3 | Vision Analyser core (PyTorch) + RAG service (ChromaDB) | ⬜ Not started | — |
| 4 | CrewAI Agentic Engine + AWS Bedrock wiring | ⬜ Not started | — |
| 5 | NeMo Guardrails configs (YAML/Colang) | ⬜ Not started | — |
| 6 | React UI + Streamlit admin panel | ⬜ Not started | — |
| 7 | n8n end-to-end orchestration | ⬜ Not started | — |

## Architecture Requirements Checklist

### Layer 1 — Frontend
- [ ] React trading dashboard: request submission, probability report display, charts, live agent logs
- [ ] Streamlit admin panel: raw data submission (reports, news, chart screenshots)

### Layer 2 — Orchestrator (n8n)
- [ ] Webhook ingestion from both frontends
- [ ] Initial payload validation (schema/required fields)
- [ ] Parallel routing to RAG + Vision services
- [ ] Synthesis call to Agentic Engine, output rail, response to UI
- [ ] Workflow JSON exports committed to `/orchestration/n8n/workflows`

### Layer 3 — Microservices (FastAPI · Docker · AWS EC2)
- [ ] **RAG Service:** ChromaDB init, HuggingFace embeddings, `/ingest`, `/query`, Bedrock/Llama.cpp summarization
- [ ] **Vision Analyser:** PyTorch ResNet-50/EfficientNet, chart screenshot → bullish/bearish condition score
- [ ] **Agentic Engine:** CrewAI team (Technical Analyst, Fundamental Analyst, Risk Manager) → structured JSON report
- [ ] **Guardrails Service:** NeMo input rails (off-topic/illegal assets) + output rails (no guarantees, no hallucinated metrics)
- [x] All services: `GET /health` + `GET /ready`, Dockerfile each, docker-compose for local dev (Step 2, 2026-06-10)
- [ ] Deployed to AWS EC2 with Docker

### Layer 4 — LLM Layer
- [ ] AWS Bedrock integration (primary)
- [ ] Local Llama.cpp/Ollama path for designated local tasks
- [ ] Provider abstraction (Bedrock ↔ local swappable via config)

### Documentation & Grading Compliance
- [x] `/docs` directory with all 4 mandatory files (Step 1, 2026-06-10)
- [x] `ARCHITECTURE.md` initial version
- [x] `CHANGELOG.md` initialized with Step 1 entry
- [x] `PROMPT_ENGINEERING_LOG.md` template + prompt-family index
- [ ] Prompt log: **n8n extractor** — ≥5 iterations + 10-case pass rate
- [ ] Prompt log: **Agent roles** — ≥5 iterations + 10-case pass rate
- [ ] Prompt log: **RAG retrieval** — ≥5 iterations + 10-case pass rate
- [ ] Prompt log: **Guardrails** — ≥5 iterations + 10-case pass rate
- [ ] Prompt log: **Ollama UI** — ≥5 iterations + 10-case pass rate

### Domain / Test Data
- [ ] Seed data + test suites using NVDA, ESLT (Elbit), NXSN (Next Vision), TOND (Tondo Smart), CUE — realistic options scenarios

## Metrics (populated as components land)

| Metric | Target | Current |
|---|---|---|
| Prompt-log pass rate per family (10 test cases) | ≥ 8/10 | — |
| Vision condition-score eval accuracy | ≥ 80% on holdout | — |
| RAG retrieval hit rate (golden questions) | ≥ 85% | — |
| Guardrails: disallowed-input block rate | 100% on red-team set | — |
| Guardrails: false-positive block rate | < 5% | — |
| E2E latency (request → validated report) | < 30 s | — |
