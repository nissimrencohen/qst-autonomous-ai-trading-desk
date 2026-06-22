"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# anchored to the service root so tests/tools never pick up a repo-root .env
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_", env_file=_ENV_FILE, extra="ignore"
    )

    service_name: str = "agentic-engine"
    port: int = 8003
    log_level: str = "INFO"
    cors_origins: str = "*"  # comma-separated; tighten in production

    # Deployment environment.
    # "aws"  → all LLM calls go exclusively to AWS Bedrock (no fallback chain).
    # anything else → multi-provider fallback chain defined by llm_provider_chain.
    environment: str = "dev"

    # ---------------------------------------------------------------- engines
    # "crew"          = CrewAI crew via the LLM router (production)
    # "deterministic" = rule-based fallback (dev/CI/degraded mode)
    engine_backend: Literal["crew", "deterministic"] = "deterministic"
    max_runs: int = 200  # run traces kept in-memory for /runs/{id}

    # ---------------------------------------------------------------- orchestrator
    # Downstream service URLs called by the async /analyze chain. Defaults are the
    # docker-compose DNS names (the engine runs inside the trading-desk network);
    # override with AGENTIC_RAG_URL etc. for local/host runs.
    rag_url: str = "http://rag-service:8001"
    vision_url: str = "http://vision-analyser:8002"
    guardrails_url: str = "http://guardrails-service:8004"
    # Per-leg HTTP timeout for the orchestrator (RAG summary can be slow); the
    # whole chain runs in a background thread so this never blocks the response.
    orchestrator_http_timeout_s: float = 120.0

    # ---------------------------------------------------------------- bedrock
    bedrock_model_id: str = "anthropic.claude-sonnet-4-6"
    aws_region: str = "us-east-1"

    # ---------------------------------------------------------------- LLM router
    # Comma-separated provider priority (left = first tried).
    # Ignored when environment == "aws" (Bedrock used exclusively).
    # Budget-first order: free-tier → paid → local
    llm_provider_chain: str = "groq,openai,gemini,ollama"

    # When True, overrides the chain and routes ALL LLM calls to local Ollama.
    # Useful for offline testing, privacy mode, or zero-cost debugging.
    force_local_ollama: bool = False

    # Groq — fastest free-tier inference
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"

    # Google Gemini
    google_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini/gemini-2.5-flash"
    # Secondary Gemini tier for the `gemini_flash` provider in llm_provider_chain.
    # GA, much higher rate limits than the pro-preview flagship — the "next in
    # line" the cascade degrades to when the primary hits a 429/quota.
    gemini_flash_model: str = "gemini/gemini-3.5-flash"

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"

    # GitHub Models (OpenAI-compatible endpoint, PAT auth)
    github_api_key: SecretStr = SecretStr("")
    github_model: str = "gpt-4o-mini"
    github_base_url: str = "https://models.inference.ai.azure.com"

    # Local Ollama — last resort in fallback chain
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"

    # ---------------------------------------------------------------- Helicone (proxy-based caching + cost analytics)
    # Set AGENTIC_HELICONE_API_KEY to route Groq/OpenAI calls through the
    # Helicone proxy.  Leave empty to call providers directly (default).
    helicone_api_key: SecretStr = SecretStr("")
    # When True (default), sends Helicone-Cache-Enabled: true on every proxied
    # request so repeated identical prompts are served from Helicone's cache.
    helicone_cache_enabled: bool = True

    # ---------------------------------------------------------------- Langfuse (self-hosted prompt tracing)
    # Set AGENTIC_LANGFUSE_PUBLIC_KEY + AGENTIC_LANGFUSE_SECRET_KEY to enable.
    # Leave empty to disable Langfuse entirely (default).
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://langfuse:3000"

    # ---------------------------------------------------------------- evaluation hooks (Step 4)
    # "deepeval" → run LLM-as-judge metrics (faithfulness + answer_relevancy)
    # "schema"   → deterministic schema-compliance check only (default; no LLM calls)
    # "none"     → disable all evaluation hooks entirely
    eval_backend: Literal["deepeval", "schema", "none"] = "schema"

    # Optional override for the evaluation judge model.
    # If set (e.g. "gpt-4o" or "groq/llama-3.3-70b-versatile"), this model is
    # used exclusively for LLM-as-judge evals, leaving the main app's LLM router
    # untouched.  Leave empty to reuse the first available provider in
    # llm_provider_chain (zero extra configuration).
    eval_judge_model: str = ""

    # Arize Phoenix self-hosted evaluation UI endpoint.
    # Set to e.g. "http://phoenix:6006" to enable Phoenix eval reporting.
    # Leave empty (default) to skip Phoenix entirely.
    phoenix_endpoint: str = ""

    # ---------------------------------------------------------------- web search
    web_search_enabled: bool = True
    # Tavily gives higher-quality financial results; falls back to DuckDuckGo if empty.
    tavily_api_key: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- market data / finance tools
    # When True, the specialist agents (Volatility, Options Flow, Space Economy,
    # Technical, Fundamental) get yfinance/VIX/options/launch tools.
    finance_tools_enabled: bool = True

    # ---------------------------------------------------------------- MCP (Model Context Protocol)
    # Exposes live technical + fundamental market data as standards-compliant MCP
    # tools (app/mcp_server.py) and wires them into the CrewAI data-gathering step.
    mcp_enabled: bool = True
    # False (default): in-process MCP tools — fast, zero-subprocess, most stable.
    # True: genuine end-to-end MCP round-trip via crewai_tools.MCPServerAdapter
    #       over stdio (requires the `mcpadapt` extra; falls back to in-process).
    mcp_crew_adapter: bool = False
    # Optional Finnhub key for the News & Geopolitical analyst (news/sentiment).
    # yfinance + thespacedevs need no key; Finnhub is an optional enrichment.
    finnhub_api_key: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- memory
    # "sqlite"   = local SQLite file (default)
    # "memory"   = in-process dict, no persistence (CI/tests)
    # "dynamodb" = AWS DynamoDB (production cloud)
    memory_backend: Literal["sqlite", "memory", "dynamodb"] = "sqlite"
    memory_db_path: str = "./data/agent_memory.db"
    dynamodb_table: str = "trading-desk-memory"
    dynamodb_region: str = "us-east-1"

    # ---------------------------------------------------------------- v1.4: run store backend
    # "memory"   = in-process OrderedDict, lost on restart (default)
    # "postgres" = durable PostgreSQL (multi-worker safe); requires postgres_dsn
    run_store_backend: Literal["memory", "postgres"] = "memory"
    postgres_dsn: SecretStr = SecretStr("postgresql+psycopg2://trading:trading@postgres:5432/trading_desk")

    # ---------------------------------------------------------------- v1.4: broker (Alpaca)
    # Paper trading (default): AGENTIC_ALPACA_PAPER=true
    # Live trading:            AGENTIC_ALPACA_PAPER=false + production key+secret
    alpaca_key: SecretStr = SecretStr("")
    alpaca_secret: SecretStr = SecretStr("")
    alpaca_paper: bool = True

    @property
    def alpaca_base_url(self) -> str:
        return "https://paper-api.alpaca.markets" if self.alpaca_paper else "https://api.alpaca.markets"

    # ---------------------------------------------------------------- v1.4: market data
    # Comma-separated provider order. First available key wins.
    # "polygon,alpaca,yfinance" — Polygon primary, Alpaca secondary, yfinance fallback
    market_data_chain: str = "polygon,alpaca,yfinance"
    polygon_api_key: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- v1.4: batch concurrency
    # Max simultaneous CrewAI crews in a /analyze/batch call.
    # CrewAI has concurrency issues with AgentExecutors so this must be 1.
    batch_concurrency: int = 1

    # ---------------------------------------------------------------- v1.4: daily briefing scheduler
    # Fires every weekday at 10:00 AM ET (30 min after US market open).
    briefing_scheduler_enabled: bool = True
    briefing_db_path: str = "./data/agent_memory.db"
    # Seconds to wait for all 8 crew runs before timing out
    briefing_poll_timeout_s: float = 300.0

    # ---------------------------------------------------------------- v1.4: authentication (JWT)
    # Set AGENTIC_AUTH_ENABLED=true to protect /analyze and /synthesize endpoints.
    # AGENTIC_AUTH_SECRET: random 32+ byte secret (openssl rand -hex 32)
    # AGENTIC_AUTH_ADMIN_PASSWORD: password for the single "admin" user
    auth_enabled: bool = False
    auth_secret: SecretStr = SecretStr("changeme-generate-with-openssl-rand-hex-32")
    # Seed passwords for the default admin / standard users. Applied ONLY when the
    # users table is empty (first boot). Defaults are dev-friendly; override in prod.
    auth_admin_password: SecretStr = SecretStr("admin")
    auth_user_password: SecretStr = SecretStr("user")
    # SQLite file backing the DB-backed RBAC users table.
    users_db_path: str = "./data/users.db"

    # ---------------------------------------------------------------- social media pipeline
    # Set to False to disable the background Reddit/Telegram ingestion entirely.
    social_pipeline_enabled: bool = True
    # Polling interval in seconds (default 900 = 15 minutes).
    social_poll_interval_s: int = 900

    # Reddit (asyncpraw) — script-app credentials from https://www.reddit.com/prefs/apps
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "TradingDesk:v1.0 (read-only research bot; contact: your@email.com)"
    # Comma-separated subreddit names (without "r/")
    reddit_subreddits: str = "wallstreetbets,investing,stocks,Daytrading"
    reddit_post_limit: int = 25    # hot posts per subreddit per cycle
    reddit_comment_limit: int = 5  # top comments per post (0 to disable)

    # Telegram (Pyrogram) — from https://my.telegram.org
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    # Optional bot token (BotFather) — use instead of user-API if preferred
    telegram_bot_token: str = ""
    # Comma-separated public channel usernames (without "@")
    telegram_channels: str = ""
    telegram_history_limit: int = 20  # messages per channel per cycle

    # ---------------------------------------------------------------- 1-minute continuous ingestion engine (Step 2d)
    # Lightweight background scheduler: fetches News, TA signals, Macro, and
    # Competitor data for all 10 watchlist tickers every INGESTION_INTERVAL_S
    # seconds and persists to SQLite + RAG vector store.
    ingestion_enabled: bool = True
    ingestion_interval_s: int = 60             # 1-minute cycle
    ingestion_db_path: str = "./data/ingestion.db"
    ingestion_concurrency: int = 3             # max parallel yfinance calls
    ingestion_prune_hours: int = 48            # discard data older than this
    # Tavily enriched news cadence — queried at most once per this interval
    # per ticker to control API costs (default 1800 s = 30 min).
    ingestion_tavily_interval_s: int = 1800

    # ---------------------------------------------------------------- continuous synthesis loop (Step 2e)
    # Sequential round-robin: one CrewAI synthesis per SYNTHESIS_INTERVAL_S,
    # reading EXCLUSIVELY from the ingestion cache (no live APIs). Default OFF to
    # protect LLM budgets in dev/CI — enable via AGENTIC_SYNTHESIS_LOOP_ENABLED=true.
    synthesis_loop_enabled: bool = False
    synthesis_interval_s: int = 150            # one ticker every 2.5 min → ~25 min/cycle
    synthesis_horizon_days: int = 7
    synthesis_report_db_path: str = "./data/synthesis_reports.db"
    synthesis_news_limit: int = 5              # cached news rows per ticker for the briefing
    synthesis_macro_stale_minutes: int = 15    # flag cached macro/VIX older than this
    synthesis_skip_unchanged: bool = False     # skip a ticker if no new ingested data

    @property
    def reddit_subreddits_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    @property
    def telegram_channels_list(self) -> list[str]:
        return [c.strip() for c in self.telegram_channels.split(",") if c.strip()]


settings = Settings()
