# Changelog

All notable changes to the Autonomous AI Trading Desk are documented here,
newest first. Format loosely follows [Keep a Changelog](https://keepachangelog.com/):
**Added** / **Changed** / **Fixed** / **Docs**.

## [Unreleased]

_(Step 5 — NeMo Guardrails — pending)_

## [0.4.0] — 2026-06-10 · Step 4: CrewAI Agentic Engine + Bedrock

### Added
- `POST /synthesize`: RAG + Vision payloads → `ProbabilityReport` (validated probabilities summing to 1.0, technical/fundamental views, risk assessment with position caps, mandatory non-empty caveats).
- `CrewEngine` (production): CrewAI sequential crew — Technical Analyst, Fundamental Analyst, Risk Manager — on AWS Bedrock, `output_pydantic`-enforced JSON, authoritative fields overwritten server-side.
- `DeterministicEngine` (dev/CI/degraded): rule-based synthesis with the same contract — vision tilt × confidence drives probability mass; binary-catalyst detection (Phase 1b/trial/tender/FDA) forces high risk + ≤2% position cap.
- `GET /runs/{run_id}`: thread-safe in-memory run-trace store (bounded LRU) powering the dashboard's agent-log panel.

### Fixed
- Binary-catalyst regex missed sub-phases like "Phase 1b" (`\b` cannot match between "1" and "b"); caught by `test_binary_catalyst_caps_position`.

### Docs
- `PROMPT_ENGINEERING_LOG.md`: **Family 2 (Agent roles)** complete — V1→V5 (chart-feature hallucination, world-knowledge blending, JSON-shape failures, risk-discipline gaps) + 10-case evaluation, 9/10.

### Tests
- 8 tests: report contract, bullish tilt, graceful no-vision degradation, binary-catalyst caps, trace retrieval, 404s. 8/8 passing.

## [0.3.0] — 2026-06-10 · Step 3: Vision Analyser + RAG core logic

### Added
- **Vision Analyser:** `POST /analyse` (multipart: ticker + chart image) → condition score in [-1, 1], label, 5 pattern probabilities, confidence. Two backends behind `VISION_MODEL_BACKEND`: `torch` (ChartConditionNet — ResNet-50 backbone, score + multi-label pattern heads, `app/model.py`) and `heuristic` (deterministic ink-centroid trend extraction for dev/CI/degraded mode). Transfer-learning trainer at `training/train.py` (freeze→unfreeze schedule, CSV manifest).
- **RAG Service:** `POST /ingest` + `POST /query` with lifespan-managed backends. Stores: `ChromaStore` (persistent ChromaDB, HF `all-MiniLM-L6-v2`, cosine HNSW, ticker-filtered) / `InMemoryStore` (dev/CI). Summarizers: `BedrockSummarizer` (Anthropic messages API), `OllamaSummarizer`, `ExtractiveSummarizer` (deterministic fallback). Grounded summarization prompt in `app/prompts.py`.
- Seed corpus `data/seed/financial_docs.json` — 15 fictional-but-realistic docs across NVDA, ESLT, NXSN, TOND, CUE (earnings, options flow, research) + `scripts/seed_rag.py`.
- Real readiness probes: vision checks analyser load; RAG checks store ping + summarizer init.
- docker-compose: chroma/heuristic backends wired via env, AWS cred passthrough for Bedrock summarization.

### Docs
- `PROMPT_ENGINEERING_LOG.md`: **Family 3 (RAG retrieval & summarization)** complete — V1→V5 with named failure modes (hallucinated metrics, advice leakage, refusal-shape instability) and 10-case evaluation, pass rate 9/10.

### Tests
- Vision: 8 tests (synthetic uptrend/downtrend/flat charts, content-type & corrupt-image rejection, readiness). RAG: 6 tests (ingest counts, ticker-filtered retrieval, grounded extractive summary with attribution, empty-result contract, validation). 14/14 passing.

## [0.2.0] — 2026-06-10 · Step 2: Microservice scaffolding

### Added
- Scaffolded all 4 FastAPI services (`rag-service` :8001, `vision-analyser` :8002, `agentic-engine` :8003, `guardrails-service` :8004) via `scripts/scaffold_services.py`, each with:
  - `GET /health` (liveness) + `GET /ready` (dependency probes, 503 on failure)
  - 12-factor config (`pydantic-settings`, per-service env prefix), structured JSON logging to stdout
  - Pinned `requirements.txt`, `python:3.12-slim` Dockerfile with `HEALTHCHECK`, `.dockerignore`, `.env.example`
  - Smoke tests (`tests/test_health.py`) — 8/8 passing in isolated per-service pytest runs
- Root `docker-compose.yml`: 4 services + n8n (:5678) on a shared bridge network, named volumes for ChromaDB and n8n state; validated with `docker compose config`.
- Root `.venv` dev toolchain (fastapi, uvicorn, pydantic-settings, httpx, pytest).

## [0.1.0] — 2026-06-10 · Step 1: Repository & documentation initialization

### Added
- Initialized git repository and full directory skeleton: `/docs`, `/services/{rag-service, vision-analyser, agentic-engine, guardrails-service}`, `/frontend/{trading-dashboard, admin-panel}`, `/orchestration/n8n/workflows`, `/infra`, `/data/seed`, `/scripts`, `/tests`.
- `docs/ARCHITECTURE.md` — 4-layer system design, service port map, planned endpoints, data flow, Docker/deployment conventions.
- `docs/TODO_AND_METRICS.md` — requirement checklist mapped to the 7-step execution plan; updated after every verified step.
- `docs/PROMPT_ENGINEERING_LOG.md` — mandatory iteration-log template (V1 baseline → V2/V3 failure-mode iterations → V4/V5 refinement → 10-case pass rate) and index of the 5 prompt families to be tuned.
- Root `README.md` and `.gitignore` (Python, Node, Docker, model artifacts, secrets).
