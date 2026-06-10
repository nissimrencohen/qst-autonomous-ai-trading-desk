# TODO & Metrics — Requirement Checklist

> **Rule:** This file is updated after every successful, verified step.
> Legend: `[x]` done & verified · `[~]` in progress · `[ ]` not started

## Execution Plan Status

| Step | Scope | Status | Verified |
|---|---|---|---|
| 1 | Repo structure + `/docs` initialization | ✅ Done | 2026-06-10 |
| 2 | Scaffold 4 FastAPI services (health checks + Dockerfiles) | ✅ Done | 2026-06-10 |
| 3 | Vision Analyser core (PyTorch) + RAG service (ChromaDB) | ✅ Done | 2026-06-10 |
| 4 | CrewAI Agentic Engine + AWS Bedrock wiring | ✅ Done | 2026-06-10 |
| 5 | NeMo Guardrails configs (YAML/Colang) | ✅ Done | 2026-06-10 |
| 6 | React UI + Streamlit admin panel | ✅ Done | 2026-06-10 |
| 7 | n8n end-to-end orchestration | ✅ Done | 2026-06-10 |

## Architecture Requirements Checklist

### Layer 1 — Frontend
- [x] React trading dashboard: request submission, probability report display, charts, live agent logs (Step 6, 2026-06-10)
- [x] Streamlit admin panel: raw data submission (reports, news, chart screenshots) (Step 6, 2026-06-10)

### Layer 2 — Orchestrator (n8n)
- [x] Webhook ingestion from both frontends (Step 7, 2026-06-10)
- [x] Initial payload validation (schema/required fields) (Step 7)
- [x] Parallel routing to RAG + Vision services (Step 7)
- [x] Synthesis call to Agentic Engine, output rail, response to UI (Step 7)
- [x] Workflow JSON exports committed to `/orchestration/n8n/workflows` (Step 7)

### Layer 3 — Microservices (FastAPI · Docker · AWS EC2)
- [x] **RAG Service:** ChromaDB init, HuggingFace embeddings, `/ingest`, `/query`, Bedrock/Llama.cpp summarization (Step 3, 2026-06-10)
- [x] **Vision Analyser:** PyTorch ResNet-50/EfficientNet, chart screenshot → bullish/bearish condition score (Step 3, 2026-06-10)
- [x] **Agentic Engine:** CrewAI team (Technical Analyst, Fundamental Analyst, Risk Manager) → structured JSON report (Step 4, 2026-06-10)
- [x] **Guardrails Service:** NeMo input rails (off-topic/illegal assets) + output rails (no guarantees, no hallucinated metrics) (Step 5, 2026-06-10)
- [x] All services: `GET /health` + `GET /ready`, Dockerfile each, docker-compose for local dev (Step 2, 2026-06-10)
- [ ] Deployed to AWS EC2 with Docker

### Layer 4 — LLM Layer
- [x] AWS Bedrock integration (primary) — RAG summarizer + CrewAI LLM, env-switchable (Step 4)
- [x] Local Llama.cpp/Ollama path for designated local tasks — RAG `OllamaSummarizer` + admin-panel pre-ingest summarizer (Step 6)
- [x] Provider abstraction (Bedrock ↔ local swappable via config) — backend matrix in ARCHITECTURE.md §2

### Documentation & Grading Compliance
- [x] `/docs` directory with all 4 mandatory files (Step 1, 2026-06-10)
- [x] `ARCHITECTURE.md` initial version
- [x] `CHANGELOG.md` initialized with Step 1 entry
- [x] `PROMPT_ENGINEERING_LOG.md` template + prompt-family index
- [x] Prompt log: **n8n extractor** — ≥5 iterations + 10-case pass rate (9/10, 2026-06-10)
- [x] Prompt log: **Agent roles** — ≥5 iterations + 10-case pass rate (9/10, 2026-06-10)
- [x] Prompt log: **RAG retrieval** — ≥5 iterations + 10-case pass rate (9/10, 2026-06-10)
- [x] Prompt log: **Guardrails** — ≥5 iterations + 10-case pass rate (10/10, 2026-06-10)
- [x] Prompt log: **Ollama UI** — ≥5 iterations + 10-case pass rate (9/10, 2026-06-10)

### Domain / Test Data
- [x] Seed data + test suites using NVDA, ESLT (Elbit), NXSN (Next Vision), TOND (Tondo Smart), CUE — realistic options scenarios (`data/seed/financial_docs.json`, Step 3)

## Metrics (populated as components land)

| Metric | Target | Current |
|---|---|---|
| Prompt-log pass rate per family (10 test cases) | ≥ 8/10 | n8n 9/10 · Agents 9/10 · RAG 9/10 · Guardrails 10/10 · Ollama UI 9/10 |
| Vision condition-score eval accuracy | ≥ 80% on holdout | heuristic backend: 3/3 synthetic directions correct; torch holdout pending labeled data |
| RAG retrieval hit rate (golden questions) | ≥ 85% | 100% on E2E golden questions (memory store; chroma eval pending corpus growth) |
| Guardrails: disallowed-input block rate | 100% on red-team set | 100% (6/6, eval 2026-06-10) |
| Guardrails: false-positive block rate | < 5% | 0% on 4-case legit set (small n) |
| E2E latency (request → validated report) | < 30 s | **1.50 s** (local chain, dev backends, `scripts/e2e_local.py`) |
