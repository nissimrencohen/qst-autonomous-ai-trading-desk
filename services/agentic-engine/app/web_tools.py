"""Web search tools for CrewAI agents.

Returns a list of crewai-compatible tool objects. Preference order:
  1. Tavily (AGENTIC_TAVILY_API_KEY set) — higher-quality financial results
  2. SerperDev (AGENTIC_SERPER_API_KEY set) — Google Search fallback
  3. EXA (no key required in crewai_tools >= 2.x)

Returns [] when AGENTIC_WEB_SEARCH_ENABLED=false so agents keep
their normal RAG-only behaviour in dev/CI.
"""
from __future__ import annotations

import logging
import os

from app.config import settings

log = logging.getLogger(__name__)


def build_search_tools() -> list:
    """Return the best available web-search tool as a single-item list, or []."""
    if not settings.web_search_enabled:
        log.debug("Web search disabled (AGENTIC_WEB_SEARCH_ENABLED=false)")
        return []

    tavily_key = settings.tavily_api_key.get_secret_value()
    if tavily_key:
        try:
            from crewai_tools import TavilySearchTool

            os.environ.setdefault("TAVILY_API_KEY", tavily_key)
            tool = TavilySearchTool()
            log.info("Web search: using Tavily")
            return [tool]
        except Exception as exc:
            log.warning("Tavily tool init failed (%s); trying fallback", exc)

    try:
        from crewai_tools import EXASearchTool

        tool = EXASearchTool()
        log.info("Web search: using EXA (no key)")
        return [tool]
    except Exception as exc:
        log.warning("EXA tool init failed (%s); web search unavailable", exc)
        return []
