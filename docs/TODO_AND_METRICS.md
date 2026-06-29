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

### v1.4 "Production Hardening & Execution Engine" — Phase Status

| Phase | Scope | Status | Verified |
|---|---|---|---|
| 1 | Execution Gatekeeper + Alpaca broker router | ✅ Done | 2026-06-17 |
| 2a | Durable RunStore (PostgreSQL + PgRunStore factory) | ✅ Done | 2026-06-17 |
| 2b | Agent concurrency — batch_orchestrator + POST /analyze/batch | ✅ Done | 2026-06-17 |
| 2c | Market data redundancy (Polygon → Alpaca → yfinance) | ✅ Done | 2026-06-17 |
| 2d | JWT authentication (opt-in, backwards compatible) | ✅ Done | 2026-06-17 |
| 3 | AWS EC2 deployment | [ ] Not started | — |

### v2.0 "Continuous Multi-Ticker Architecture" — Step Status (gated)

| Step | Scope | Status | Verified |
|---|---|---|---|
| 1 | Architecture gap analysis (audit current vs V2.0 requirements) | ✅ Done | 2026-06-18 |
| 2a | Strict watchlist: `app/watchlist.py` single source of truth (10 symbols) + input-level rejection (422) on `/analyze` + `/synthesize` | ✅ Done | 2026-06-18 |
| 2b | Data tools: Competitor (`fetch_competitors` + `COMPETITOR_MAP`) + Macro S&P/NASDAQ (`fetch_macro_snapshot`) + VIX; CrewAI `get_macro_snapshot`/`get_competitor_analysis` tools | ✅ Done | 2026-06-18 |
| 2c | Mandatory macro & fear context (`app/macro_context.build_desk_context`) injected into every `/analyze`, not just briefing (Req 2) | ✅ Done | 2026-06-18 |
| 2d | 1-minute ingestion engine (`ingestion_engine.py` + `IngestionStore`): quote/news/TA(RSI,MACD,BB)/macro/VIX/competitors/Tavily → SQLite + RAG; Semaphore(3), 16 tests | ✅ Done | 2026-06-18 |
| 2e | Continuous synthesis loop (`synthesis_loop.py`): sequential round-robin (1 ticker/150s), offline store-backed tools (`offline_tools.py`), `ReportStore`, `/synthesis/*` endpoints; 20 tests + offline-crew integration PASS (yfinance-raise decoupling proof) | ✅ Done | 2026-06-18 |
| 2f | CrewAI prompt updates — competitor-tool mandate + per-ticker macro/fear factoring (prompts.py + engine.py); PROMPT_ENGINEERING_LOG Family 2 → V6 (10/10); live AAPL/NVDA/VIXY crew integration PASS | ✅ Done | 2026-06-18 |
| 2g | Frontend + RAG realignment: RAG `updater.ACTIVE_TICKERS` + `market-live` focus → 10-symbol watchlist; new React "Live Continuous Desk" (`ContinuousDesk.tsx`) polling `/synthesis/latest` with embedded Macro/VIX block; `TICKERS`/`VOL_TICKERS` realigned. Frontend build clean | ✅ Done | 2026-06-18 |
| 3 | Component testing — ingestion (16 tests: dedup/indicators/rate-limit) + synthesis (20 tests: offline tools/decoupling/report store) | ✅ Done | 2026-06-18 |
| 4 | Integration testing — single ticker end-to-end: 2f live crew (AAPL/NVDA/VIXY, competitor+VIX) and 2e offline crew (AAPL, yfinance-raise decoupling proof) | ✅ Done | 2026-06-18 |

### v2.0 Forensic Bug Fixes (gated, sequential)

| Bug | Scope | Status | Verified |
|---|---|---|---|
| 2 | Technical Analyst hallucination — TA (RSI/MACD/BB) never reached agents. New `ta_indicators.py` + live `get_technical_indicators` tool; Technical Analyst cites real values; `_build_rag_from_store` injects quote+TA. PROMPT_LOG → V7. Live: probs diverge (MSFT 0.55 vs 0.40) | ✅ Done | 2026-06-19 |
| 1 | Daily Briefing migrated to offline path — shared `synthesize_ticker_offline()`; reads IngestionStore + offline crew, no rag-service/orchestrator. Verified: 10/10 `done`, zero error with deps on a dead port | ✅ Done | 2026-06-19 |
| 3 | DeterministicEngine tilts from cached TA when no chart — `ta_signal` on SynthesizeRequest + `_tilt_from_ta_signal` (RSI/MACD/BB). Verified: bullish spread 0.00→0.42, VIXY correctly bearish, zero LLM | ✅ Done | 2026-06-19 |
| 4 | Calibration (2-part): DeterministicEngine neutral-compression + crew manager/SYNTHESIS_TASK break-the-anchor directive (PROMPT_LOG V8). Verified: det spread 0.51; crew 3/3 broke anchor (AMZN 0.65/0.25/0.10, VIXY 0.15/0.25/0.60) | ✅ Done | 2026-06-19 |
| 5 | `_build_rag_from_store` circular citation — now extracts publisher/url from meta_json | ✅ Done | 2026-06-19 |
| UI | UI outage fix: ErrorBoundary (no more app-wide white-screen), new Ingestion Dashboard + `/ingestion/status`, stale tickers → watchlist, briefing NaN guards. All 3 dashboards verified rendering live (zero console errors) | ✅ Done | 2026-06-19 |

### v1.3 "Live Forecast Desk" — Phase Status

| Phase | Scope | Status | Verified |
|---|---|---|---|
| 1 | Async run/poll pipeline — fix timeout + `/market-live` 404; n8n thin dispatch; `/market-live` cache; durable env-key fix | ✅ Done | 2026-06-17 |
| 2 | Frontend persistence (localStorage history/restore) + `GET /memory/{ticker}` | ✅ Done | 2026-06-17 |
| 3 | Predictive GBM charts (history + Monte-Carlo bands, drift tilted by crew probabilities) | ✅ Done | 2026-06-17 |
| 4 | VIX-regime / market-heat alerts + intraday vs multi-day flows | ✅ Done | 2026-06-17 |
| 5 | Live mega cross-referenced analysis (SPCX, AAPL/MSFT/NVDA/GOOGL/AMZN, UVXY under one VIX regime) | ✅ Done | 2026-06-17 |
| 6 | Docs refresh + strict system evaluation | ✅ Done | 2026-06-17 |

### EVAL Research Lab — Phase Status

| Phase | Scope | Status | Verified |
|---|---|---|---|
| 1 | Eval hooks (`eval_hooks.py`): schema_compliance (deterministic) + faithfulness/answer_relevancy (DeepEval LLM-as-judge). Async ThreadPoolExecutor, Langfuse + Phoenix score posting. Config: `AGENTIC_EVAL_BACKEND`, `AGENTIC_EVAL_JUDGE_MODEL`. Unit tests (`test_eval_hooks.py`, 5 tests). | ✅ Done | 2026-06-29 |
| 2 | Benchmark runner (`scripts/run_eval_matrix.py`): Golden Dataset (5 prompts × VIXY/SVXY/SPCX), 3×3 matrix (SwarmSize × Model), serial execution with provider fallback, JSONL output. `POST /eval/synthesize` endpoint + `eval_schemas.py` (SwarmSize, EvalConfig, EvalSynthesizeRequest). Dynamic swarm sizing in `engine.py` (SOLO/TRIAD/FULL). | ✅ Done | 2026-06-29 |
| 3 | Data aggregation (`scripts/aggregate_eval_data.py`): JSONL + Langfuse REST API + Phoenix REST API → `dashboard_ready_data.json`. `GET /eval/summary` API endpoint. Quality composite (50% faithfulness + 30% relevancy + 20% schema). Auto-generated best-config conclusions. | ✅ Done | 2026-06-29 |
| 4 | EVAL Lab dashboard (`EvalDashboard.tsx`): React workspace fetching `GET /eval/summary`. Bar charts, scatter plots, conclusions panel. Wired into `App.tsx` as a new tab. | ✅ Done | 2026-06-29 |

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
- [x] Execution Gatekeeper — whitelist enforcement + Alpaca broker routing (v1.4, 2026-06-17)
- [x] Durable RunStore — PostgreSQL backend with PgRunStore, SQLAlchemy, JSONB (v1.4, 2026-06-17)
- [x] Agent concurrency — POST /analyze/batch, asyncio.Semaphore, 7 tickers in parallel (v1.4, 2026-06-17)
- [x] Market data redundancy — Polygon.io primary, Alpaca secondary, yfinance fallback (v1.4, 2026-06-17)
- [x] JWT authentication — opt-in, HS256, POST /auth/token, Depends(require_auth) (v1.4, 2026-06-17)
- [x] EVAL hooks — post-synthesis schema_compliance (deterministic) + faithfulness + answer_relevancy (DeepEval LLM-as-judge), async ThreadPoolExecutor, Langfuse + Phoenix integration (EVAL, 2026-06-29)
- [x] EVAL Research Lab — `POST /eval/synthesize` + `GET /eval/summary` endpoints, dynamic SwarmSize (SOLO/TRIAD/FULL), benchmark runner + aggregation pipeline (EVAL, 2026-06-29)
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

## Cold-Start Verification Log

| Date | Result | Notes |
|---|---|---|
| 2026-06-17 | ✅ 34/34 passed · smoke PASSED | Full cold-start: compose down → up; all 7 endpoints healthy; happy path 98.5 s (cold Ollama); insider blocked ✅ |

## Metrics (populated as components land)

| Metric | Target | Current |
|---|---|---|
| Prompt-log pass rate per family (10 test cases) | ≥ 8/10 | n8n 9/10 · Agents 9/10 · RAG 9/10 · Guardrails 10/10 · Ollama UI 9/10 · Macro ctx 8/10 |
| Vision condition-score eval accuracy | ≥ 80% on holdout | heuristic backend: 3/3 synthetic directions correct; torch holdout pending labeled data |
| RAG retrieval hit rate (golden questions) | ≥ 85% | 100% on E2E golden questions (memory store; chroma eval pending corpus growth) |
| Guardrails: disallowed-input block rate | 100% on red-team set | 100% (6/6, eval 2026-06-10) |
| Guardrails: false-positive block rate | < 5% | 0% on 4-case legit set (small n) |
| E2E latency (request → validated report) | < 30 s | **1.50 s** dev backends · **~22–35 s** crew/Gemini warm (measured 2026-06-17) · ⚠ local Ollama path ~68–98 s (CPU-bound, acceptable for demo) |
| /market-live latency (warm cache) | < 2 s | **0.005 s** (90 s TTL cache, computed in worker thread) |
| Run/poll: time to run_id | < 1 s | **0.029 s** median (POST /analyze → {run_id}) |
| Eval schema_compliance pass rate | 100% on valid reports | 100% (deterministic; unit tests 5/5 pass) |
| Eval hook latency (schema-only) | < 0.1 s | ~0.01 s (no LLM calls in default backend) |
| Eval hook latency (deepeval) | < 30 s | ~15–25 s (2–3 LLM judge calls) |

## Strict System Evaluation — אבולוציה קשוחה (2026-06-17)

> No-sugarcoat assessment of every architectural weak point as of v1.3 Phase 5
> completion. These are real limitations, not hypothetical.

### ✅ What works well

- **Async run/poll fixes the timeout**: the original `TimeoutError: signal timed out`
  is gone. The browser gets a `run_id` in <30 ms and polls. No client-side timeout
  fires even for slow crews (~35 s).
- **`/market-live` cache**: cold 8.7 s (25 yfinance calls) → warm 0.005 s. The
  Command Center loads instantly on every visit.
- **Multi-provider LLM router**: Groq (free tier, fast) → Gemini (AQ. key, workhorse)
  → OpenAI → Ollama. Automatic fallback on quota exhaustion.
- **Guardrails**: 100% red-team block rate (insider, manipulation, sanctioned assets);
  0% false positives on legit set (small n). Output rail catches hallucinated metric
  claims absent from RAG evidence.
- **GBM forecast**: closed-form lognormal bands render correctly in SVG; drift tilt
  from crew probabilities is mechanically correct. Honest non-predictor.
- **Persistence layer**: localStorage history, server-side `agent_memory.db`
  (volume-backed), alert log — all survive container restart (except RunStore).
- **Regime alerting**: VIX/heat diff on each 90 s refresh fires correctly;
  persisted to `desk01.alerts.v1`; toast dismisses after 8 s.

### ⚠ Known weak points

**1. In-memory `RunStore` — not durable**
`RunStore` is a bounded dict in-process. Container restart wipes all run traces.
`GET /runs/{id}` returns 404 for any run from before the restart. The dashboard's
`AgentLog` shows "awaiting run…" for restored reports. Mitigation: persist
`RunStore` to `agent_memory.db` (same SQLite, already volume-backed). Not done.

**2. CrewAI executor concurrency limit**
`CrewEngine` is a singleton. Concurrent `crew.kickoff()` calls raise
`RuntimeError: Executor is already running`. Phase 5 mega-run exposed this —
6/7 concurrent submissions failed; only MSFT (first in) completed. Mitigation:
add a per-analysis `asyncio.Lock` or run each crew in a fresh subprocess/worker.
Not done. Current workaround: sequential submission from the client.

**3. yfinance as sole market-data dependency**
`yfinance` is an unofficial reverse-engineered API. It breaks on rate limiting,
ticker symbol changes, and Yahoo Finance backend updates without notice. `/market-live`
has no fallback when yfinance fails — it returns 500. Several tickers in the desk
(`NXSN`, `TOND`) have thin yfinance coverage. Mitigation: add a secondary provider
(e.g. `polygon-api` or `alpha_vantage`) with a graceful partial-response mode.

**4. GBM forecast is naive**
Closed-form lognormal: constant drift and vol from recent history, no
regime-switching, Gaussian returns (thin tails), no mean-reversion for VIX
products. SPCX has insufficient history (<20 bars). UVXY's forecast is misleading
(GBM doesn't model the VIX ETF's structural contango decay). Labelled as
"not a precision predictor" in the UI. This is correct framing but users may
over-weight the visual.

**5. `max_position_pct` over-conservative in macro batch**
When SPCX (2% IPO cap) is present in the macro context alongside large-caps,
Gemini over-applies the conservative cap across all names (all showed 2% in Phase 5).
The correct behavior is ×0.72 scaling of each name's unconditioned cap. Prompt V5
addresses this partially; residual is model calibration. Mitigation: post-process
`max_position_pct` server-side using the regime discount before returning the report.

**6. Social pipeline coverage gap**
Reddit/Telegram returned no signals for AAPL/MSFT/AMZN/GOOGL in Phase 5 (the
pipeline is seeded for the desk's original tickers: NVDA/ESLT/CUE etc.).
The crew correctly caveated "community sentiment unavailable" but the signal is
always empty for megacap names. Mitigation: expand subreddits to include
r/stocks, r/options, r/AAPL, r/googl etc.

**7. No authentication on any service endpoint**
All four FastAPI services and the nginx proxy are open on the host network.
`POST /analyze` with an adversarial `macro_context` can inject arbitrary text
into the crew's inputs. Mitigation: add API-key auth middleware on `POST /analyze`
and `POST /synthesize` at minimum. Not done (single-host dev environment only).

**8. n8n as extra hop / single point of failure**
The n8n webhook is the primary path, with a fallback to direct `/analyze`.
But n8n itself is an extra failure surface: workflow updates require a headless
`docker exec` + import + publish cycle. If n8n's state container corrupts
(it has happened during Docker Desktop restarts), the primary path is dead until
a fresh import. Mitigation: treat direct `/analyze` as equally primary; deprecate
the n8n layer for the production path.

**9. Options flow data is public-OI approximation**
`get_options_sentiment` reads public option chain data. The crew labels reads
as "approximation from public data" (enforced via OPTIONS_FLOW_ANALYST backstory),
but the UI renders them alongside confirmed fundamentals. A user reading quickly
may not catch the distinction. Mitigation: add a visual "⚠ approximation" badge
in the Fundamental card for options-flow derived drivers.

**10. No multi-worker safety on `RunStore` / `agent_memory.db`**
`uvicorn` in production is configured for a single worker. Running two workers
(e.g. `--workers 2`) would give each worker its own `RunStore` instance, making
`GET /runs/{id}` return 404 on the wrong worker. `agent_memory.db` has no
write-locking beyond SQLAlchemy's session scope. Mitigation: move `RunStore` to
Redis or the existing SQLite DB with proper locking before scaling beyond 1 worker.
