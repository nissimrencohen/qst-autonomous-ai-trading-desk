# Changelog

All notable changes to the Autonomous AI Trading Desk are documented here,
newest first. Format loosely follows [Keep a Changelog](https://keepachangelog.com/):
**Added** / **Changed** / **Fixed** / **Docs**.

## [Unreleased]

_(v1.4 follow-ups: EC2 deployment, GARCH/Heston forecast, social pipeline expansion, max_pos post-processing)_

### EVAL Research Lab — 4-phase LLMOps evaluation system

**Added**
- **Phase 1 — Eval hooks** (`app/eval_hooks.py`): three post-synthesis metrics
  computed asynchronously in a background `ThreadPoolExecutor(max_workers=2)`:
  `schema_compliance` (deterministic: probs sum, confidence range, risk_level
  enum, caveats non-empty), `faithfulness` (LLM-as-judge via DeepEval), and
  `answer_relevancy` (LLM-as-judge via DeepEval). Results posted to Langfuse
  (Score objects on the synthesis trace) and Arize Phoenix
  (`/v1/trace_annotations`). Config: `AGENTIC_EVAL_BACKEND` (`schema` |
  `deepeval` | `none`), `AGENTIC_EVAL_JUDGE_MODEL` (optional pinned judge).
  Unit tests: `test_eval_hooks.py` (5 tests, all deterministic).
- **Phase 2 — Benchmark runner** (`scripts/run_eval_matrix.py`): Golden Dataset
  of 5 VIX/options/derivatives prompts × 3 swarm sizes (SOLO/TRIAD/FULL) × 3
  models = 45 cells. Strictly serial execution with per-provider fallback
  (openai → groq → github → gemini_flash → gemini). Results saved as JSONL.
- **Phase 2 — Schemas** (`app/eval_schemas.py`): `SwarmSize` enum
  (SOLO/TRIAD/FULL), `EvalConfig` (experiment_name, run_label, swarm_size,
  target_model, skip_fallback), `EvalSynthesizeRequest` (superset of
  `SynthesizeRequest`). Dynamic swarm sizing in `engine.py`.
- **Phase 2 — EVAL API** (`app/api.py`): `POST /eval/synthesize` — runs the
  analyst crew with a dynamic `EvalConfig` and returns a `ProbabilityReport`.
  Same guardrails/gatekeeper as production for score comparability.
- **Phase 3 — Data aggregation** (`scripts/aggregate_eval_data.py`): fuses JSONL
  + Langfuse REST API + Phoenix REST API into `dashboard_ready_data.json`.
  Quality composite: 50% faithfulness + 30% relevancy + 20% schema_compliance.
  Auto-generated best-config conclusions. `GET /eval/summary` API endpoint
  serves the aggregated payload to the frontend.
- **Phase 4 — EVAL Lab dashboard** (`EvalDashboard.tsx`): React workspace
  accessible via the EVAL LAB tab. Fetches `GET /eval/summary` and renders
  bar charts, cost comparison, quality scatter, and conclusions panel.

**Docs**
- `SYSTEM_OVERVIEW.md`: added EVAL Research Lab section (§9), updated all
  diagrams (system architecture, component, swarm, data flow), updated
  observability (§13) with eval pipeline integration, added UC-12 (EVAL
  benchmark), added 6 glossary terms.
- `ARCHITECTURE.md`: updated Layer 1–4 diagrams, added EVAL endpoints, added
  eval backend to backend matrix, updated data flow and repository layout.
- `TODO_AND_METRICS.md`: added EVAL Research Lab phase tracker, added eval
  checklist items, added eval metrics.

### Rebrand → QST · real chart vision · per-tab product clarity

**Changed**
- **Product rebrand → QST (Quant Swarm Terminal)**: wordmark `QST` (amber) + neo-mint terminal-cursor accent, subtitle "QUANT SWARM TERMINAL · 360° MULTI-AGENT MARKET INTELLIGENCE". Applied to masthead, login, chat assistant label, browser `<title>`, aria/title.
- **Per-tab product framing** (`components/ModeBanner.tsx`): every workspace now declares its purpose + the pipeline behind it via a provenance chip — **LIVE DESK** = `CONTINUOUS · CACHED` (always-on monitoring from the 1-min ingestion cache), **ANALYSIS** = `LIVE · MCP + VISION` (on-demand deep-dive: live MCP data + chart vision + 7-agent crew), **BRIEFING** = `MORNING · GBM MODEL`. Makes the three views' very different data-freshness/sourcing explicit.

**Fixed / Enabled**
- **Real chart vision**: `vision-analyser` was running the `heuristic` backend (returns a meaningless ~neutral score; chart never truly "read"). Switched to **`VISION_MODEL_BACKEND=llm`** (gpt-4o-mini → gemini escalation; OpenAI key already in the container). Verified: a real NVDA candlestick returns `bullish, conf 0.75, backend=llm:gpt-4o-mini` and now flows through `/analyze`.

**Added**
- `scripts/make_chart.py` — PIL-only candlestick renderer from yfinance OHLC (no matplotlib), used to exercise the LLM vision path end-to-end.

### Chart-vision surfaced in report/UI + IngestionStore thread-safety

**Fixed**
- **Uploaded chart analysis was invisible**: the orchestrator ran vision and fed it to the crew, but the result was never written to `ProbabilityReport`, so the dashboard showed no sign the chart was used (looked "failed"). Added `vision` to the `ProbabilityReport` schema, attached it in `orchestrator.py`, and added a **Chart Vision** card to `ReportView` (label + score + confidence + pattern chips, coloured by direction). Verified: SPCX chart → `bearish, conf 0.70` now shown.
- **IngestionStore concurrency bug**: a single SQLite connection was shared, unguarded, across the ingestion writer + 1-s synthesis-loop reader + briefing → "bad parameter or other API misuse" / "recursive use of cursors" (broke SPCX and intermittently others in LIVE DESK / BRIEFING). Added a `threading.RLock` around all connection access; offline-store read failures dropped from constant → 0.

**Note**
- Corrected an earlier wrong assumption: **SPCX is a real, live ticker** (Space Exploration Technologies; yfinance returns price/volume/fundamentals — 52w 149.34–225.64). The data layer was always connected; the issues were the invisible vision result + the store concurrency bug.

### MCP (Model Context Protocol) market-data integration + Golden Run

**Added**
- **MCP server** (`app/mcp_server.py`): a standards-compliant FastMCP server exposing two tools — `get_technical_data` (price, volume, %chg, RSI, MACD, Bollinger) and `get_fundamental_data` (P/E, EPS, market cap, name, 52w range). Runnable over stdio (`python -m app.mcp_server`) for any MCP client (Claude Desktop, IDEs, CrewAI adapter).
- **Shared data layer** (`app/mcp_data.py`): resilient backing functions reused by both the MCP server and the in-process CrewAI tools, so the protocol path and the in-process path return identical payloads. Built on the existing market-data chain (polygon→alpaca→yfinance) + shared TA math.
- **CrewAI wiring** (`app/mcp_tools.py`): `build_mcp_tools()` returns in-process MCP tools (default, stable) with an opt-in `MCPServerAdapter` stdio round-trip path (`AGENTIC_MCP_CREW_ADAPTER=true`). Wired into `engine.py` so the analysts invoke MCP for technical/fundamental data; MCP **supersedes** the overlapping legacy `get_market_quote` / `get_technical_indicators` tools so data genuinely routes through MCP.
- **Isolated test** (`test_mcp_data.py`): drives the real FastMCP protocol (list_tools/call_tool) for AAPL + NVDA, validating JSON shape and fields; plus a time-boxed stdio round-trip.
- Config: `AGENTIC_MCP_ENABLED` (default true), `AGENTIC_MCP_CREW_ADAPTER` (default false); compose env + `mcp>=1.6.0` requirement.

**Fixed**
- **CrewEngine live-synthesis crash**: `crew_kickoff` logging referenced a non-existent `self._agents` (`AttributeError`), breaking every `/analyze`, `/analyze/batch` and `/synthesize` call. Corrected to the local `agents` dict.

**Verified (Golden Run, 2026-06-21)**
- Isolated MCP test passed (valid JSON for AAPL/NVDA, no rate-limit crashes).
- Live batch over all 10 desk tickers: 9 done / 1 gatekeeper-flagged / 0 errors, with **22 MCP tool invocations** logged — grounded MCP data cut hallucination flags from 3→1.
- Daily briefing regenerated; `agent_memory.db` / `ingestion.db` populated; React dashboard renders all views (AlphaSwarm branding, RBAC tabs, briefing for all 10 instruments).

### Final Production Polish — DB-backed RBAC · legacy eradication · UI/UX overhaul

**Added**
- **DB-backed RBAC** (`services/agentic-engine/app/users.py`): real `users` table (SQLite) with `username`, `hashed_password`, `role` (admin/user); bcrypt hashing (direct `bcrypt`, not passlib — passlib 1.7.x breaks on bcrypt ≥ 4.1). Seeds a default `admin` + `user` on startup **only when the table is empty**.
- **JWT login + role gating** (`app/auth.py`): `POST /auth/token` verifies credentials against the DB and returns `{access_token, token_type, role, username}`; the JWT carries a `role` claim. New `current_user` / `require_admin` dependencies + `GET /auth/me`. Config: `AGENTIC_AUTH_USER_PASSWORD`, `AGENTIC_USERS_DB_PATH`.
- **React auth flow**: `src/auth/AuthContext.tsx` (login/logout, token + role persisted to localStorage, cross-tab sync) and a centered, terminal-aesthetic `src/components/Login.tsx`. The whole desk now sits behind login.
- **Route protection**: `user` role → LIVE DESK, BRIEFING, ANALYSIS MODE, ASSISTANT. `admin` role → all of the above **plus** the admin-only INGEST tab (hidden from the nav for standard users and guarded at render).
- Tests: DB login returns role, `require_admin` 403s a standard user, JWT now encodes/decodes the role claim.

**Changed**
- **Branding**: header wordmark `DESK/01` → **DEEP ALPHA ENGINE** (amber + neo-mint accent), refined responsive sizing aligned with the sub-header. Masthead now shows the signed-in user, role badge, and a LOGOUT control.
- **LIVE DESK polish**: roomier card padding + grid gutters; all numeric surfaces use tabular (fixed-width) figures so columns align.

**Fixed**
- **CRITICAL nav-tab hover bug**: the active tab painted amber-on-amber on hover (invisible). Active+hover now renders dark ink on the amber fill; inactive hover is bright white on a subtle tint.

**Removed**
- **Legacy Streamlit admin panel** fully eradicated: deleted `frontend/admin-panel/`, removed the `admin-panel` service from `docker-compose.yml`, and purged port `8501` from the live docs. Manual ingestion lives in the dashboard's admin-only INGEST → MANUAL UPLOAD tab.

### Golden Master — branding (AlphaSwarm) + vision-based UI overhaul

**Changed**
- **Product branding → AlphaSwarm**: header wordmark is now `ALPHA` (amber) + `SWARM` (neo-mint) with subtitle "AUTONOMOUS MULTI-AGENT QUANT DESK · 360° MARKET INTELLIGENCE". Applied across the masthead, login screen, chat assistant label, browser `<title>`, and aria/title attributes. Refined, responsive display sizing aligned with the sub-header.
- **Briefing panel overhaul** (`DailyBriefingPanel.tsx`): converted from RTL Hebrew + off-palette blue (`#3b82f6`) / slate fallback tokens to **English, LTR, and the real amber/neo-mint terminal palette** (`--amber`, `--bull`, `--bear`, `--ink*`, `--line*`). Tabular numerals across the data grid; roomier card padding and grid gutters; dates pinned to `en-US`.

**Verified**
- Legacy check: `frontend/admin-panel` absent; `8501` / `admin-panel` absent from `docker-compose.yml`.
- `tsc -b` + `vite build` clean; masthead wordmark colors, tabular numerals, active-tab hover legibility, and the de-Hebraized briefing confirmed in the browser preview.

## [2.0.0-dev] — Continuous Multi-Ticker Architecture · *in progress (gated)*

> V2.0 upgrades the desk from on-demand single-ticker analysis to a continuous,
> autonomous multi-ticker desk. Built step-by-step; each step verified + documented
> before the next.

### Step 2a — Strict Watchlist: single source of truth + input-level rejection (Req 1)

**Added**
- **`app/watchlist.py`** — canonical single source of truth for the approved **10** instruments: `SPCX MSFT AAPL NVDA GOOGL AMZN UPRO TQQQ VIXY SVXY`. Exposes `WATCHLIST`, `WATCHLIST_ORDERED`, `VOL_TICKERS={VIXY,SVXY}`, `INSTRUMENT_LABELS`, and helpers `normalize()`, `is_whitelisted()`, `assert_whitelisted()`, `is_volatility_instrument()`.
- **Strict input rejection**: `field_validator` on `AnalyzeRequest.ticker` and `SynthesizeRequest.ticker` rejects off-list tickers with **HTTP 422 before any work begins**, and normalises (`$aapl` → `AAPL`).
- Tests: `TestStrictTickerValidation` (off-list 422 on `/analyze` + `/synthesize`; new members UPRO/TQQQ/VIXY accepted; `$aapl` normalised end-to-end).

**Changed**
- **Watchlist composition (mission-aligned): added UPRO, TQQQ, VIXY; removed UVXY**; removed bare VIX proxy aliases (`VIX`/`^VIX`/`VXX`) from the accept-list — the fear index is now covered via mandatory macro/VIX *context* (Req 2), not as a tradeable member.
- `app/gatekeeper.py`: `WHITELIST` + `is_whitelisted` now **re-exported from `app.watchlist`** (duplicate definition deleted).
- `app/engine.py`: `CrewEngine._VOL_TICKERS` → `watchlist.VOL_TICKERS` (`{VIXY, SVXY}`).
- `app/daily_briefing.py`: `_BRIEFING_TICKERS` → all 10 from `WATCHLIST_ORDERED`; volatility-desk routing → `VOL_TICKERS`.
- `app/api.py`: `BatchAnalyzeRequest` default → all 10; `max_length` 7 → 10.
- `app/batch_orchestrator.py`: docstring 7 → 10 instruments.
- Tests updated: whitelist membership (10 symbols), VIX aliases now rejected, `/gatekeeper/whitelist` length 10.

**Deferred to later V2.0 steps (tracked, not lost):**
- rag-service `market-live` focus list + `updater.ACTIVE_TICKERS` still reference UVXY/^VIX/demo tickers → frontend/RAG-alignment step.
- `prompts.VOLATILITY_ANALYST` still says "^VIX or UVXY" → prompt-update step (requires a PROMPT_ENGINEERING_LOG entry).
- `social_signal_processor` recognised-ticker universe lacks UPRO/TQQQ/VIXY → data-sources step.

### Step 2b/2c — Data layer: Competitor + Macro + VIX tools, mandatory macro/fear context (Req 2 & 3)

**Added**
- **Competitor read-through** — `COMPETITOR_MAP` + `competitors_for()` in [`app/watchlist.py`](services/agentic-engine/app/watchlist.py) (e.g. NVDA → AMD/AVGO/INTC/TSM; SPCX → RKLB/ASTS/LMT/BA; leveraged/vol ETFs map to 1x/2x/inverse siblings). `finance_tools.fetch_competitors(ticker)` returns each peer's live price + daily % change.
- **Broad-market macro snapshot** — `finance_tools.fetch_macro_snapshot()`: S&P 500 (`^GSPC`→`SPY`) + NASDAQ (`^IXIC`→`QQQ`) level, daily % change, and a risk-on/risk-off tone.
- **Two new CrewAI tools** wired into the finance toolset (auto-available to the 5 finance-tool agents): `get_macro_snapshot`, `get_competitor_analysis`. (VIX tool `get_vix_curve` already existed.)
- **`app/macro_context.py`** — `build_desk_context(ticker)` composes the macro snapshot + VIX curve into one **mandatory** block. 60 s TTL cache on the (market-wide) data so a 10-ticker burst triggers ≤1 macro+VIX fetch (rate-limit safe).

**Changed**
- **Mandatory Macro & Fear context (Req 2):** [`orchestrator.py`](services/agentic-engine/app/orchestrator.py) now builds `build_desk_context()` and injects it into the existing `{macro_context}` placeholder for **every** `/analyze` — previously only the daily briefing supplied it. A caller-supplied `macro_context` (e.g. the briefing's richer block) is respected. New `macro_context` run-trace step records source (`caller`/`desk_auto`).
- All fetchers are **rate-limit resilient**: any provider error → `{"error": ...}`; `build_desk_context` always returns a non-empty block (degrades to "unavailable").

**Tested** — new [`tests/test_data_layer.py`](services/agentic-engine/tests/test_data_layer.py) (12 tests, mocked yfinance — no network): macro parsing + ETF fallback + 429-resilience; competitor read-through + per-peer resilience + unmapped/normalised input; `build_desk_context` completeness, graceful degradation, and cross-ticker caching; orchestrator injects macro on every analysis and respects caller-supplied context. **Full suite: 70 passed, 1 skipped.**

> Note: agent prompt templates are **not** yet updated to instruct use of the competitor tool — that is Step 2f (with a PROMPT_ENGINEERING_LOG entry), per the gated plan. Macro/fear is already deterministically injected, so it does not depend on prompt changes.

### Step 2f — Prompt updates: competitor tool usage + per-ticker macro/fear factoring (Req 2 & 3)

**Changed (`app/prompts.py`)**
- **Fundamental Analyst** now MUST call `get_competitor_analysis`, cite ≥1 peer (`[peers: yfinance]`), and explicitly tie the macro/fear backdrop to the ticker's fundamentals.
- **Technical Analyst** now calls `get_competitor_analysis` for relative-strength context and weights its read by the macro/fear regime.
- **Volatility Analyst** — corrected stale lead-instrument reference (`^VIX`/`UVXY` → `VIXY`/`SVXY`, post-watchlist) with roll-decay / inverse-roll notes (clears the Step-2a deferral).
- **`SYNTHESIS_TASK`** — added two MANDATORY report inclusions: (1) how the broad-market + VIX/fear regime affect *this* ticker; (2) a peer read-through citing ≥1 competitor (or "no mapping").

**Changed (`app/engine.py`)** — the macro/fear block is now injected into the **technical and fundamental analyst task descriptions** (previously synthesis-only), and the fundamental/technical tasks explicitly instruct the competitor-tool call + peer citation.

**Tested — live single-ticker integration (mission Step 4 / Step 5 self-correction)** via new [`scripts/integration_competitor_macro.py`](scripts/integration_competitor_macro.py), real CrewAI desk (`engine_backend=crew`):
- **AAPL / NVDA / VIXY all PASS** — `get_competitor_analysis` actively fired 2–3× per run (peers: MSFT/GOOGL/AMZN, AMD/AVGO/INTC/TSM, UVXY/VXX/VIXM/SVXY); each final report discussed the macro/fear backdrop (risk-off, VIX/regime, populated `volatility_view`).
- **Rate-limit resilience proven:** Groq hit its daily token cap mid-run; the LLM router fell back to OpenAI and completed without failing the analysis.
- Fixed a Windows cp1252 console crash by making the macro block ASCII (`→`/`·` → `->`/`|`). Full deterministic suite remains green.

**Prompt log:** [`PROMPT_ENGINEERING_LOG.md`](docs/PROMPT_ENGINEERING_LOG.md) Family 2 advanced to **V6** (10/10) — failure modes, changes, and a 10-case evaluation (3 live full-crew + 4 component + 3 adversarial/honesty).

### Step 2d — 1-Minute Continuous Ingestion Engine (Req 4)

**Added**
- **`app/ingestion_store.py`** — `IngestionStore` (SQLite): `IngestionRow` data model, `upsert` (SHA1 dedup / `INSERT OR IGNORE`), `query_latest` / `query_since` (time-ordered), `prune`, `count`. Indexed by `(ticker, ingested_at)`.
- **`app/ingestion_engine.py`** — 60 s background loop (asyncio): per ticker fetches **quote** (A1), **news**, pure-Python **TA** (RSI-14 / MACD-12,26,9 / Bollinger-20,2σ), **competitors**, plus market-wide **macro** (ticker `MACRO`) + **VIX** (ticker `VIX`); slow-cadence **Tavily** news (≤ once / 30 min / ticker). `asyncio.Semaphore(3)` rate-limit guard; every fetcher degrades to `[]` on error. New rows dual-written to SQLite + RAG `/ingest`.
- **`config.py`** `ingestion_*` settings; lifespan start/stop in `main.py`; `conftest.py` pins `INGESTION_ENABLED=false`.
- **16 component tests** (`tests/test_ingestion.py`): dedup, indicator math, fetch edge-cases, rate-limit resilience.
- **Step 2e A1 patch:** ingestion now also stores the primary ticker's own quote/fundamentals (`source_type='quote'`, EPS/PE/market-cap via the resilient market-data chain) so the offline `get_market_quote` is fully populated.

### Step 2e — Continuous Synthesis Loop (Req 4)

**Added**
- **`app/offline_tools.py`** — store-backed CrewAI tools mirroring the live tool *names* (`get_market_quote`, `get_technical_indicators`, `get_vix_curve`, `get_macro_snapshot`, `get_competitor_analysis`; options/launch return explicit "unavailable in continuous mode"). **Imports neither yfinance, requests, nor finance_tools** — decoupling is structural. Pure readers (`offline_*`) wrapped by the `@tool` factory.
- **`app/report_store.py`** — `ReportStore` (SQLite): latest `ProbabilityReport` per ticker + round-robin `cursor`, `heartbeat`, and `last_seen` (skip-unchanged) meta.
- **`app/synthesis_loop.py`** — sequential round-robin background task: one ticker every `synthesis_interval_s` (default **150 s → ~25 min/cycle**). Per ticker: store-built RAG briefing + store-backed macro/fear context + offline crew → output rail → gatekeeper → persisted to RunStore **and** ReportStore. Never fires all 10 at once.
- **`build_synthesis_engine(store)`** (engine.py) + `CrewEngine(offline_store=…)` — the loop's crew binds the offline toolset and **no live web search**; the on-demand `/analyze` path is unchanged.
- **`build_desk_context_from_store()`** (macro_context.py) — mandatory macro/fear block sourced from the cached `MACRO`/`VIX` rows, with a staleness flag (shared formatter with the live builder).
- **Endpoints:** `GET /synthesis/latest`, `GET /synthesis/latest/{ticker}`, `GET /synthesis/status`.
- **`config.py`** `synthesis_*` settings — **`synthesis_loop_enabled` defaults `false`** (opt-in via env to protect LLM budgets; decision B1); `conftest.py` pins it off.

**Changed** — `main.py` lifespan starts/stops the loop + owns `app.state.report_store`.

**Tested** — **20 component tests** (`tests/test_synthesis_loop.py`): offline tools incl. **decoupling (yfinance patched to raise → tools still serve cache)**, store-backed macro context (complete / stale / empty), report store CRUD + cursor + last_seen, and the single-ticker runner. **Full suite: 109 passed, 1 skipped.**
- **Integration (`scripts/integration_synthesis_loop.py`):** real offline crew for AAPL with **`yfinance.Ticker` patched to RAISE** — full 7-agent run completed, report persisted to ReportStore, macro/fear discussed (VIX/MACRO/REGIME/RISK-OFF), options correctly "unavailable offline". **PASS — zero live calls.** (Groq daily cap hit → OpenAI fallback completed it.)

### Step 2g — Frontend + RAG realignment to the watchlist

**RAG cleanup (legacy demo tickers removed)**
- `rag-service/app/updater.py`: `ACTIVE_TICKERS` `[GOOGL, SPCX, ^VIX, UVXY]` → the **10-symbol watchlist**; VIX term-structure now fetched once per cycle as a market-wide macro doc (^VIX is no longer a tradeable list member).
- `rag-service/app/api.py` `/market-live`: `focus` ticker set `[NVDA, SPCX, UVXY, GOOGL, CUE, ESLT]` → the **10 watchlist** symbols; stale "Sell UVXY short" advice and the `uvxy_signal` semantics re-pointed to VIXY/SVXY.

**Frontend — "Live Continuous Desk" (new)**
- **`components/ContinuousDesk.tsx`** — polls `GET /synthesis/latest` (+ `/synthesis/status`) every 30 s and renders one card per ticker from the autonomously generated reports (probabilities, risk, execution side, confidence, top caveat) with a live status/heartbeat bar.
- **Data integrity:** each card renders the **Macro & Fear context block** embedded in the continuous report — S&P 500 / NASDAQ level + tone and VIX level / term-structure / regime.
- `api.ts`: `fetchSynthesisLatest()` + `fetchSynthesisStatus()`. `types.ts`: `SynthesisReport` / `SynthesisLatest` / `SynthesisStatus` / `SynthesisMacro`; **`TICKERS` and `VOL_TICKERS` realigned to the 10-symbol watchlist** (fixes the instrument selector + ticker tape). `App.tsx`: new **`LIVE DESK`** view mode + header toggle.

**Backend support**
- `report_store.py`: persists a structured **`macro` block** (macro snapshot + VIX) alongside each report (`macro_json` column + safe migration); `/synthesis/latest` returns it. `synthesis_loop.py` passes `offline_macro_snapshot`/`offline_vix_curve` into `save()`.

**Verified** — frontend `npm run build` (tsc + vite) **clean**; agentic suite **109 passed, 1 skipped** (incl. new `test_synthesize_one_persists_macro_block`); rag-service **6 passed**; data-integrity check confirms `/synthesis/latest` payload carries `macro.macro` (S&P/NASDAQ) + `macro.vix` (regime).

**Live demo** — backend (deterministic engine, `SYNTHESIS_LOOP_ENABLED=true`, 10 s interval) + Vite ran a full round-robin: the desk rendered **10/10 LIVE** cards with macro/fear blocks (live S&P +0.95% / NASDAQ +1.34% / VIX ~17 contango ELEVATED, refreshed mid-rotation) and an advancing heartbeat; **0 errors/tracebacks** (only expected degrade-open notices, guardrails/rag not started). Added reusable demo tooling: `scripts/demo_seed_ingestion.py` + `.claude/launch.json`. Minor UI polish: VIX value rounded to 1 dp in `ContinuousDesk`.

### Bug fixes (post-V2.0 forensic pass · gated)

**Bug #2 — Technical Analyst hallucinated (TA disconnected from synthesis)** 🔴
- *Root cause:* the ingestion engine computed RSI/MACD/Bollinger and stored them, but they never reached the agents — `_build_rag_from_store` pulled only news, and no prompt instructed the offline `get_technical_indicators` tool (the technical task hinged on the always-"unavailable" offline vision payload). The analyst fabricated chart narrative ("consolidation, bullish divergence") and the mega-caps collapsed to a flat 40/40/20.
- *Fix:* new **`app/ta_indicators.py`** (single source of truth for the indicator math; `ingestion_engine` re-exports the old `_compute_*` names) + a **live `get_technical_indicators`** tool in `finance_tools` (symmetric with the offline one). `TECHNICAL_ANALYST` + technical task rewritten to ALWAYS call it and **cite the actual values** (vision payload demoted to supplementary; never invent patterns). `_build_rag_from_store` now injects the cached **quote + TA** into the briefing.
- *Verified live* (`scripts/verify_bug2_ta.py`, offline crew vs real cache): `get_technical_indicators` fires per ticker; reads now cite real numbers (GOOGL: *"neutral RSI of 51.85…"*; MSFT: *"above the upper Bollinger Band → overbought"*); probabilities **diverge by the data** — MSFT 0.55 vs NVDA/GOOGL 0.40 (whose cached TA is genuinely near-identical). PROMPT_ENGINEERING_LOG Family 2 → **V7**. Suite: **110 passed, 1 skipped**.

**Bug #1 — Daily Briefing not migrated to the offline path** 🟠
- *Root cause:* the briefing fired the **live orchestrator** (`run_analysis_job` → rag-service `/query` over HTTP) per ticker. When rag-service was down every instrument returned `status:"error"`, `crew` bull/neut/bear = 0/null (only the GBM move-probs populated).
- *Fix:* `run_daily_briefing` now runs the **offline path** — extracted a shared `synthesis_loop.synthesize_ticker_offline()` primitive (used by both the continuous loop and the briefing) that reads the `IngestionStore` + offline crew, with **no rag-service / orchestrator dependency**. VIX/regime now sourced from the cache (`_vix_from_store_or_live`); move-probs kept (yfinance, wrapped degrade-safe). Briefing reports `data_source: "ingestion_cache (offline)"`.
- *Verified* (`scripts/verify_bug1_briefing.py`) with **rag-service + guardrails pointed at a dead port**: all **10 instruments `status:done`, zero `error`**, crew signals populated. Suite: **110 passed, 1 skipped**. (Flat 0.333 under the deterministic engine is Bug #3, tracked next; the crew engine yields varied probabilities.)

**Bug #3 — DeterministicEngine flatlined at 33/33/33 in continuous mode** 🟠
- *Root cause:* the deterministic tilt was `vision.score × confidence`; with no chart (always, in the offline loop) tilt=0 → 33/33/33, ignoring the cached TA.
- *Fix:* new `ta_signal` field on `SynthesizeRequest` (the loop passes the cached RSI/MACD/BB meta); new `engine._tilt_from_ta_signal()` derives a monotonic tilt (RSI→`(rsi−50)/50`, MACD cross ±1, Bollinger position) → `bull=⅓+0.35·tilt`. The deterministic `technical_view` now cites the real indicators, and risk/confidence account for the cached technicals. CrewEngine ignores the field (it pulls TA via `get_technical_indicators`).
- *Verified* (`scripts/verify_bug3_deterministic.py`, all 10, zero LLM): **bullish spread 0.00 → 0.42** — AMZN/MSFT 0.57–0.58 (RSI 60+, above-band), VIXY **0.16** (bearish MACD/lower-half). `neutral` stays ⅓ by design (tilt only shifts bull↔bear). Suite: **110 passed, 1 skipped**.

**Bug #4 — Probability calibration (break the 40/40/20 anchor)** 🟡
- *Two-part fix:*
  - **DeterministicEngine — neutral compression:** `neutral = max(0.10, ⅓ − 0.233·|tilt|)`, freed mass split to the favored side by `bull_frac = ½ + ½·tilt`. Strong setups now compress neutral and push the winner up (was a rigid ⅓ neutral).
  - **Crew/LLM manager — break the anchor:** `QUANT_EXECUTION_MANAGER` + `SYNTHESIS_TASK` now explicitly instruct: when TA + macro + competitor signals align, push the favored side >0.60 and cut the opposing side to 0.05–0.10 (drop the ~0.20 floor); stay balanced only on mixed/thin evidence. PROMPT_ENGINEERING_LOG Family 2 → **V8**.
- *Verified:* deterministic (`verify_bug3_deterministic.py`) — bullish spread **0.51**, neutral 0.17 on strong names; crew (`verify_bug4_calibration.py`) — **3/3 broke the anchor**: AMZN 0.65/0.25/0.10, VIXY 0.15/0.25/0.60, NVDA 0.55/0.35/0.10. Suite: **110 passed, 1 skipped**.

**Bug #5 — circular citation in `_build_rag_from_store`** 🟢
- Fixed: news/tavily summary lines and the retrieved-doc `source` now extract the real `publisher` / `url` from `meta_json` (`_src()` helper) instead of citing the headline as its own source.

### UI outage fix (full-stack)
- *Root causes:* (a) **no `ErrorBoundary`** — a single component crash white-screened ALL views; (b) the **Ingestion Dashboard + `/ingestion/status` never existed**; (c) `CommandCenter` + `DailyBriefingPanel` referenced **stale demo tickers** (UVXY/ESLT/CUE/^VIX) that V2.0 removed; (d) `DailyBriefingPanel` rendered `NaN%` when the offline briefing's `move_probs` were empty; (e) the backend/loops weren't running to populate the cache.
- *Fixes:* `IngestionStore.stats()` + resilient **`GET /ingestion/status`** (never 500s); new **`IngestionDashboard.tsx`** (totals, source breakdown, per-ticker freshness) + `INGEST` header view; **`ErrorBoundary.tsx`** wrapping every view; CommandCenter/briefing tickers → watchlist; briefing `move_probs` null-guards (`ProbBar` + up/down split).
- *Verified live* (full stack, ingestion + synthesis loop enabled, deterministic): all **three dashboards render real V2.0 data with ZERO console errors** — Ingestion (170 rows, 10 tickers fresh, running every 60 s), Live Desk (10/10, calibrated e.g. AMZN 71/17/12), Briefing (10/10 `done`, MSFT 70 % bull). Frontend build clean (46 modules); backend **110 passed, 1 skipped**.

## [1.4.0] — 2026-06-17 · Production Hardening & Execution Engine

### Added

**Phase 1 — Execution Gatekeeper & Broker Integration**
- **`app/gatekeeper.py`** — whitelist enforcement module. `enforce(report, run_id)` intercepts every `ExecutionPlan` after synthesis. Whitelist: `SPCX MSFT AAPL NVDA GOOGL AMZN UVXY SVXY`. Non-whitelisted tickers: execution blocked, Langfuse `gatekeeper_violation` trace fired, `[GATEKEEPER] Execution BLOCKED` caveat appended to report. Analysis always passes through — only the broker call is gated.
- **Alpaca broker router** (`_submit_alpaca`): paper (default) or live via `AGENTIC_ALPACA_PAPER`. Submits market/limit orders via `alpaca-trade-api`. Degrades to a no-op stub when no key is configured. `OrderResult` dataclass carries `broker_order_id`, `status`, `timestamp`.
- **`GET /gatekeeper/whitelist`** — returns the current approved instrument list.
- Gatekeeper wired into both the async `/analyze` path (orchestrator.py) and the direct `/synthesize` path (api.py).

**Phase 2 — Architecture Hardening**
- **`app/runs_pg.py`** — `PgRunStore`: PostgreSQL-backed RunStore (psycopg2 + SQLAlchemy). Schema auto-created on startup (`run_traces` table with JSONB `steps`/`report`/`blocked_reasons`). Preserves identical `RunHandle` interface — zero caller changes. Activated via `AGENTIC_RUN_STORE_BACKEND=postgres`.
- **`build_run_store()` factory** (runs.py) — selects memory vs. postgres backend at startup. `main.py` now calls the factory instead of instantiating `RunStore()` directly.
- **`app/batch_orchestrator.py`** — `run_batch()`: concurrent analysis of up to 7 whitelisted tickers via `asyncio.gather` + `asyncio.Semaphore(AGENTIC_BATCH_CONCURRENCY=3)`. Each ticker runs `run_analysis_job` in its own `asyncio.to_thread`. Fixes the Phase 5 `RuntimeError: Executor is already running` regression.
- **`POST /analyze/batch`** — new endpoint. Accepts `{"tickers": [...], "question": "...", "horizon_days": 30}`. Returns `{started: [{ticker, run_id}], skipped: [{ticker, reason}]}` immediately; callers poll `GET /runs/{id}` per ticker.
- **`app/market_data.py`** — multi-source market data with automatic fallback. Provider chain: Polygon.io → Alpaca Market Data → yfinance. Configured via `AGENTIC_MARKET_DATA_CHAIN`. `finance_tools.fetch_quote()` now delegates here — all CrewAI agents transparently use the best available source.
- **`app/auth.py`** — JWT (HS256, 8 h TTL) opt-in authentication. `POST /auth/token` (OAuth2 password form). `Depends(require_auth)` on `/analyze`, `/synthesize`, `/analyze/batch`. When `AGENTIC_AUTH_ENABLED=false` (default) the dependency is a no-op — zero breaking change.
- **`docker-compose.yml`**: added `postgres:16-alpine` service with healthcheck; `agentic-engine` `depends_on: postgres: condition: service_healthy`; new `pg_run_store` named volume. All new env vars forwarded.
- **`.env.example`** extended with v1.4 sections: RunStore, Broker, Market Data, JWT Auth, Batch Concurrency.
- **`requirements.txt`** additions: `psycopg2-binary`, `alpaca-trade-api`, `polygon-api-client`, `python-jose[cryptography]`, `passlib[bcrypt]`.

### Changed
- `app/config.py`: 10 new settings with safe defaults (`run_store_backend`, `postgres_dsn`, `alpaca_key/secret/paper`, `market_data_chain`, `polygon_api_key`, `batch_concurrency`, `auth_enabled/secret/admin_password`).
- `app/main.py`: `RunStore()` → `build_run_store()`; `auth_router` included; startup log extended with `run_store` + `auth` fields.
- `app/api.py`: full rewrite to add `BatchAnalyzeRequest`, `/analyze/batch`, `GET /gatekeeper/whitelist`, `AuthDep` on all write endpoints, gatekeeper applied on `/synthesize` path.
- `app/finance_tools.py`: `fetch_quote()` now delegates to `market_data.fetch_quote_resilient()` — Polygon/Alpaca primary, yfinance fallback.

## [1.3.5] — 2026-06-17 · Mega cross-referenced analysis — *v1.3 Phase 5*

### Added
- **`macro_context` wired into CrewAI inputs** (`engine.py`): `SynthesizeRequest.macro_context` was already in the schema but not passed to the crew. Now injected as `{macro_context}` in both the synthesis task (`SYNTHESIS_TASK` in `prompts.py`) and the `inputs` dict. The manager prompt instructs scaling `max_position_pct` to the regime's `recommended_exposure_pct`.
- **`docs/analysis/MEGA_ANALYSIS_2026-06-17.md`**: live crew analysis of 7 instruments (SPCX, NVDA, GOOGL, AAPL, MSFT, AMZN, UVXY) under one shared VIX-regime anchor (VIX=16.08, elevated, contango, market_heat=medium, 72% recommended exposure). All 7 run_ids listed and verified.
- **`interval` field on `AnalyzeRequest`** (Phase 4 backfill — now used in Phase 5 runs).

### Changed
- **`SYNTHESIS_TASK` prompt** (prompts.py): added `{macro_context}` placeholder and cross-portfolio regime scaling instruction. See PROMPT_ENGINEERING_LOG Family 6.

### Known issues documented
- **CrewAI executor concurrency**: firing 7 simultaneous `/analyze` calls fails with `RuntimeError: Executor is already running`. The singleton `CrewEngine` cannot run concurrent kickoffs — runs must be queued or submitted sequentially. MSFT completed (first), 6 others failed; all were re-run sequentially.
- **maxpos=2% for large-caps in mega batch**: the crew applied the freshly-IPO'd cap (intended for SPCX) across all names when the macro context included SPCX. Honest assessment: large-caps should receive ×0.72 scaling from their unconditioned cap, not collapse to 2%.

### Verified
- All 7 run_ids resolved to valid `ProbabilityReport` objects (engine=crew, Gemini backend).
- SPCX: bull 55%, max_pos 2% (IPO cap appropriate). AMZN: bull 60%, strongest directional read. UVXY: neutral 50% (correct for contango/decay regime).
- `macro_context` present in synthesis inputs confirmed via agent output logs.

## [1.3.4] — 2026-06-17 · Regime alerts + intraday — *v1.3 Phase 4*

### Added
- **VIX-regime / market-heat change detection** (`App.tsx`): on each 90 s `/market-live` refresh, `vix.regime` and `risk_summary.market_heat` are diffed against the previous snapshot; any change fires an `AlertEntry`.
- **Alert toast** (`AlertToast.tsx`): fixed-position banner (bottom-right, `var(--bear)` border) shown on regime/heat transitions; auto-dismisses after 8 s or on click.
- **Alert log panel** (`AlertLog.tsx`): persisted to `localStorage` (`desk01.alerts.v1`, 50-entry cap); rendered above the history bar in Analysis Mode.
- **Intraday / multi-day prediction interval toggle** (`RequestForm.tsx`): MULTI-DAY (1d bars, horizon from slider) vs. INTRADAY 5m (5-minute bars, forces `horizon_days=1`). Interval forwarded to `POST /analyze` → `build_forecast()`.
- **`interval` field on `AnalyzeRequest`** (backend `schemas.py`): `"1d"` default, `"5m"` for intraday. Threaded through `orchestrator.py` → `build_forecast(interval=...)`.

### Fixed
- Removed `tgcrypto` from `agentic-engine/requirements.txt` — optional C-extension speedup for Pyrogram's crypto that requires `gcc` (absent from the slim Python image). Pyrogram auto-falls back to pyaes.

### Verified
- Alert log: injected a `regime: elevated→stress` event via browser eval → panel renders "1 EVENT · REGIME · ELEVATED → STRESS"; survives reload.
- Interval toggle: MULTI-DAY / INTRADAY 5m buttons rendered in order ticket; horizon slider disabled in intraday mode.
- 0 console errors; tsc clean build.

## [1.3.3] — 2026-06-17 · Predictive GBM charts — *v1.3 Phase 3*

### Added
- **`build_forecast()` (`services/agentic-engine/app/forecast.py`)**: GBM closed-form lognormal p10/p50/p90 projection. Fetches yfinance history, computes annualised drift μ and vol σ from log-returns, then tilts μ by `(bullish − bearish) × 0.15 × σ` — so the chart's projected path reflects the crew's directional thesis. Supports daily (`1d`) and intraday (`5m`) intervals. Degrades gracefully to `None` on yfinance failure or thin history (<20 bars).
- **`Forecast` / `ForecastPoint` schemas** (schemas.py): `ticker`, `interval`, `model`, `anchor_price`, `drift_annual`, `vol_annual`, `directional_bias`, `history[]`, `projection[]`, `generated_at`.
- **`forecast: Forecast | None`** appended to `ProbabilityReport` — backward-compatible (existing reports validate without it).
- **Orchestrator attachment**: after synthesis + output rail, `build_forecast()` is called and the result is model-copied onto the report before storing on the `RunStore`.
- **`ForecastChart` component** (`frontend/.../ForecastChart.tsx`): pure dependency-free SVG (720×250) — solid history line, dashed amber median projection, amber-tint p10–p90 cone; price Y-axis ticks, "now" divider, legend with terminal p50 value and uncertainty range. Matches DESK/01 terminal aesthetic.
- **`Forecast` / `ForecastPoint` TypeScript interfaces** added to `types.ts`.
- **`ReportView`** renders `<ForecastChart>` between the analysis grid and the execution plan when `r.forecast` is present.

### Fixed
- **Stale `runId` console errors**: `AgentLog` was receiving `runId ?? report?.run_id ?? null`, so on page reload it polled the restored report's `run_id` against the new empty `RunStore` (7× 404). Fixed by passing only `runId` (current-session ephemeral) — never `report?.run_id`.

### Verified
- API-level: NVDA forecast — anchor $207.41, bias +0.25, p50 terminal $235.79, p10–p90 $199.50–$278.68.
- Browser (Playwright): Analysis Mode loaded → 0 console errors → RUN ANALYSIS submitted → report + forecast SVG chart rendered (confirmed via `img` element in accessibility snapshot). History bar shows two entries.

## [1.3.1] — 2026-06-17 · Persistence & memory — *v1.3 "Live Forecast Desk" Phase 2*

### Added
- **`GET /memory/{ticker}` (agentic-engine)** — surfaces the persisted per-ticker analysis history from `agent_memory.db` (the same prior turns the crew injects into the Fundamental Analyst). `app.state.memory` added to the lifespan.
- **Frontend persistence (`src/storage.ts`)** — `localStorage` for the last report, a 20-entry analysis history, and the last `/market-live` snapshot. Restored on mount so a refresh no longer wipes the desk.
- **History bar (`components/HistoryBar.tsx`)** — clickable strip of past analyses (ticker · bull% · time) that re-opens a stored report; survives reloads.

### Changed
- Command Center renders the **last cached market snapshot instantly** on load while the fresh `/market-live` fetch resolves (and during brief backend downtime).

### Verified
- `/memory/NVDA` and `/memory/GOOGL` return persisted crew turns across container restarts (volume-backed).
- Browser (Playwright): ran an NVDA analysis → **reloaded the page** → report fully restored + History chip "NVDA 50% 12:48" present; 0 console errors.

## [1.3.0] — 2026-06-17 · Async run/poll pipeline — *v1.3 "Live Forecast Desk" Phase 1*

### Fixed
- **Dashboard `TimeoutError: signal timed out`**: the browser waited synchronously on the n8n webhook, which ran the whole 6-agent crew internally before responding (minutes) → the 120 s `AbortSignal` fired, then the fallback died on `/query`'s 60 s timeout. Root fix is an async run/poll pattern (below) — the UI no longer blocks on the crew.
- **`/market-live → 404`**: the running RAG container was a stale build predating the route (`api.py:56`); rebuilt. The whole v1.2 source (finance tools, updater) was likewise undeployed until this rebuild.

### Added
- **`POST /analyze` (agentic-engine, `app/orchestrator.py`)** — self-contained async orchestrator: guardrails input rail → RAG `/query` → vision (if chart) → crew synthesis → guardrails **output** rail. Returns `{run_id}` in <30 ms and runs the blocking chain in a worker thread (`asyncio.to_thread`). Degrades open on a guardrails outage.
- **Run lifecycle on `RunStore`/`RunTrace`** — `status` (running/done/blocked/error) + `report` + `error` + `blocked_reasons`; `GET /runs/{id}` now serves the finished report so the dashboard can poll.
- **`/market-live` TTL cache** (90 s) computed in a worker thread so the ~25 yfinance calls never block the event loop; warmed at startup off the request path. Cold 8.7 s → warm **0.005 s**.

### Changed
- **n8n `TradingDeskWf001` reworked to thin async dispatch** (sole orchestrator, per design choice): kept payload validation, the Ollama ticker-extractor, and the input guardrails gate; replaced the blocking RAG→Vision→Synthesize→output-rail tail with one POST to `/analyze`, responding `{run_id}` in ~0.1–0.2 s. `volatility_desk` is now captured and forwarded (was silently dropped).
- **Frontend `analyze()` → start-and-poll**: posts to the webhook (falls back to direct `/analyze` if n8n is unreachable), then polls `GET /runs/{id}` every 1.5 s, driving the live Agent Trace; no client-side timeout. Command Center desks pre-select their ticker in the order form.
- **docker-compose LLM keys now source from the prefixed `.env` vars** (`${AGENTIC_GOOGLE_API_KEY:-${GOOGLE_API_KEY:-}}`, etc.) — permanently fixes the host-env shadowing gotcha where the shell's stale `AIza` key masked the working `AQ.` key in `.env`.
- **`updater.py` `ACTIVE_TICKERS`**: bogus `"SPACE"` → `"SPCX"`.

### Verified
- `POST /analyze` → run_id in **0.029 s**; crew completes ~22 s (NVDA) / ~34 s (GOOGL), `engine=crew`, live Gemini probe OK.
- `/market-live` 200 (warm 0.005 s); Command Center renders live (SPY/QQQ/^GSPC/^NDX, VIX regime, 6 desks) with **0 console errors**.
- n8n webhook `{run_id}` in ~0.1 s (direct :5678 + dashboard proxy :3002); insider-information request blocked at the input rail through the reworked flow.
- Full GOOGL analysis driven through the UI (Playwright): bull 55% / neutral 30% / bear 15%, vol ELEVATED, paper LONG plan (entry 373.25 / target 385 / stop 367, R/R 1.88), live agent trace — **no timeout**.

## [1.0.4] — 2026-06-17 · Output-rail + extractor bug fixes

### Fixed
- **`hallucinated_metric` false-positive for CUE/TOND**: `risk_assessment.notes` contained `"2%"` (policy text, not a market claim). The output-rail `_CLAIM_NUMBER` regex matched it, and the substring check `"2%" in evidence_blob` passed for NVDA (whose seed docs happen to include `"52%"`) but failed for tickers whose docs contain no `X2%` substring. Fix: (a) changed DeterministicEngine `notes` to use `"max position N pct."` notation instead of `%`; (b) changed n8n Guardrails Output node to check `fundamental_view.key_drivers` (actual market claims derived from RAG) instead of `risk_assessment.notes` (internal policy text).
- **Ollama extractor returns NVDA for CUE free-text**: n8n extractor prompt mapped company names before checking literal symbols. The question `"... for CUE into the monthly expiry"` was occasionally misidentified as NVDA. Fix: added explicit Rule 1 — if any known desk symbol (NVDA, ESLT, NXSN, TOND, CUE) appears literally in the text, return it immediately before any name-to-symbol mapping. Verified: extractor now returns `CUE` correctly.

### Verified
- CUE free-text (no form ticker) → extractor returns `CUE`, output_rail `pass`, full report delivered.
- CUE explicit ticker → output_rail `pass` (was blocked before fix).
- 34/34 unit tests still passing.

## [1.0.2] — 2026-06-11 · Live local LLM layer (Ollama qwen3:8b)

### Added
- RAG summarizer switched to live Ollama (`qwen3:8b`) via root `.env` (`RAG_SUMMARIZER_BACKEND=ollama`); compose passes `RAG_OLLAMA_URL/MODEL` and the admin panel's `OLLAMA_MODEL`.
- `OllamaSummarizer`: `think: false` for reasoning models (measured **223s → 23s** on qwen3:8b) with graceful retry for older Ollama, plus `<think>` block stripping as backstop.
- `scripts/smoke_llm.py` — live LLM-layer verification: real summarization, full n8n chain, free-text extraction.
- `scripts/inspect_n8n_flat_error.py` — decodes n8n 2.x flattened execution data to surface node errors headlessly.

### Fixed
- n8n 2.x blocks `$env.*` in expressions ("access to env vars denied") — extractor URL/model are now workflow constants (documented in orchestration/n8n/README.md).
- Per-service `Settings.env_file` anchored to the service root — the repo-root deployment `.env` no longer leaks into unit tests (caught by `test_rag_flow` failing from repo root only).
- nginx webhook proxy read timeout 180s → 300s for cold-model calls.

### Verified
- Live qwen3:8b RAG summary: grounded + `[source:]` attribution + Coverage line, ~29s.
- Full n8n chain (chart + LLM summarizer): report in ~68s, output rail `pass`.
- **Family-1 extractor live**: free-text "Nvidia into the monthly expiry" → `NVDA` / 30d through the production webhook (~75s). All 34 unit tests still passing from any CWD.

## [1.0.1] — 2026-06-10 · Live local deployment hardening

### Fixed
- **n8n 2.x compatibility** of the workflow export: top-level `id` (CLI import constraint), node-level `webhookId` on the Webhook node (production registration), `publish:workflow` instead of deprecated `update:workflow --active`, and try/catch around `$('Node')` references to skipped branches in "Build Synthesis Payload" (n8n 2.x throws on unexecuted-node lookups).
- Dashboard primary path: nginx now proxies `/webhook/*` to the n8n container (request-time DNS via Docker resolver, so the SPA still boots and falls back to direct mode when n8n is absent).
- Port 8003 collision after a Docker Desktop restart (another project's backend bound it first; agentic-engine recreated with its binding restored). Dashboard republished on :3002 (3000 occupied by open-webui).

### Added
- `scripts/smoke_webhook.py` — live webhook smoke test (happy + insider-blocked paths).
- `orchestration/n8n/README.md`: headless standalone-n8n deployment flow + n8n 2.x gotchas.

### Verified
- Full production-path smoke through n8n (chroma store + heuristic vision + deterministic engine + rule rails): report in **0.60s**, output rail `pass`, insider request blocked at `input_rail`. RAG corpus seeded into persistent Chroma (15 docs).

## [1.0.0] — 2026-06-10 · Step 7: n8n end-to-end orchestration — **all 7 steps complete**

### Added
- `orchestration/n8n/workflows/analyze-request.json` — importable 23-node workflow: webhook → payload validation (400 path) → optional Ollama free-text extraction (Family-1 prompt) → Guardrails input rail (blocked path) → parallel RAG ∥ Vision fan-out → merge → synthesis → Guardrails output rail (pass/sanitize/block paths) → respond. Service DNS names target the compose network.
- `orchestration/n8n/README.md` — flow diagram, import/activation steps, curl smoke test.
- `scripts/e2e_local.py` — boots all four services and replays the orchestration chain offline. **Verified: 12/12 assertions, chain latency 1.50s** (happy path NVDA + uptrend chart; negative paths: insider request blocked, hallucinated metric blocked).

### Docs
- `PROMPT_ENGINEERING_LOG.md`: **Family 1 (n8n extractor)** complete — V1→V5 (unparseable shape, ticker hallucination, horizon normalization, field discipline) + 10-case evaluation, 9/10. **All 5 prompt families now logged: 25/25 iterations, pass rates 9/10, 9/10, 9/10, 10/10, 9/10.**
- `ARCHITECTURE.md` finalized: implemented endpoints, backend matrix (production vs. dev/CI fallbacks), end-to-end data flow with degraded mode.

## [0.6.0] — 2026-06-10 · Step 6: React trading dashboard + Streamlit admin panel

### Added
- **Trading dashboard** (`frontend/trading-dashboard`, Vite + React + TS, :3000): DESK/01 terminal UI — order-ticket form (domain tickers, horizon slider, chart upload), animated probability report (bull/neutral/bear bars, technical/fundamental/risk cards, caveats), live agent-trace panel polling `/runs/{id}`, pipeline stage indicator, blocked-request rendering. Primary data path is the n8n webhook; automatic degraded direct-services mode (guardrails → RAG ∥ Vision → synthesize) when the orchestrator is down. Production build verified (`tsc -b && vite build`). Two-stage Docker build (node → nginx).
- **Admin panel** (`frontend/admin-panel`, Streamlit, :8501): single-doc + bulk-JSON RAG ingestion, Vision chart quick-score, local Ollama pre-ingest summarizer (Family-5 prompt), sidebar service-health board.
- CORS middleware (env-configurable origins) on all four services — required for browser direct mode and trace polling.
- docker-compose: `trading-dashboard` (:3000) and `admin-panel` (:8501) services.

### Docs
- `PROMPT_ENGINEERING_LOG.md`: **Family 5 (Ollama UI)** complete — V1→V5 (number corruption, shape instability, preamble, off-topic confidence) + 10-case evaluation, 9/10.

### Tests
- Full backend regression after CORS changes: 34/34 passing. React production build clean; Streamlit app syntax-checked.

## [0.5.0] — 2026-06-10 · Step 5: NeMo Guardrails

### Added
- `POST /validate/input` — blocks insider/MNPI requests, market manipulation (pump-and-dump, spoofing, front-running, wash trading), sanctioned/illicit-asset requests, malformed tickers, and off-topic questions.
- `POST /validate/output` — three-way contract: `pass` / `sanitize` (guarantee language rewritten to calibrated phrasing) / `block` (claim-like figures — `$41.2B`, `38%` — absent from the supplied evidence ⇒ hallucinated metric).
- Two backends behind `GUARDRAILS_BACKEND`: `rules` (deterministic regex rails, dev/CI/degraded) and `nemo` (same rules first, then NeMo Guardrails LLM self-check rails on Bedrock — fail-closed layering).
- NeMo configuration committed: `rails/config.yml` (self_check_input / self_check_output prompts, V5) + `rails/flows.co` (Colang refusal flows for canonical insider/manipulation/illegal-asset/off-topic phrasings).

### Docs
- `PROMPT_ENGINEERING_LOG.md`: **Family 4 (Guardrails)** complete — V1→V5 (undefined policy, over-blocking false positives, jailbreak framing, fail-open parser breakage) + 10-case red-team/legitimate evaluation, 10/10.

### Tests
- 12 tests: 6 input-rail (allow/insider/manipulation/sanctioned/off-topic/malformed-ticker), 4 output-rail (pass/sanitize/block-hallucinated/grounded-numbers), 2 ops. 12/12 passing.

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
