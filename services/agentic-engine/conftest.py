"""Test bootstrap: pin the deterministic fallbacks and expose `app.*`.

The suite is designed to run on the deterministic engine / in-memory backends
with no cloud creds (see docs + project memory). A developer's repo-root `.env`
may set `AGENTIC_ENGINE_BACKEND=crew` (plus real LLM keys), which the app's
import chain loads via `load_dotenv(override=False)`. We therefore pin the test
defaults HERE — conftest is imported before any `app` module, so `setdefault`
wins over the later, non-overriding `.env` load. An explicit env var still
overrides (e.g. to intentionally exercise the crew engine).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("AGENTIC_ENGINE_BACKEND", "deterministic")
os.environ.setdefault("AGENTIC_MEMORY_BACKEND", "memory")
os.environ.setdefault("AGENTIC_RUN_STORE_BACKEND", "memory")
os.environ.setdefault("AGENTIC_AUTH_ENABLED", "false")
os.environ.setdefault("AGENTIC_SOCIAL_PIPELINE_ENABLED", "false")
os.environ.setdefault("AGENTIC_INGESTION_ENABLED", "false")
os.environ.setdefault("AGENTIC_SYNTHESIS_LOOP_ENABLED", "false")
os.environ.setdefault("AGENTIC_BRIEFING_SCHEDULER_ENABLED", "false")
os.environ.setdefault("AGENTIC_LLM_PROVIDER_CHAIN", "openai")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")

sys.path.insert(0, str(Path(__file__).parent))
