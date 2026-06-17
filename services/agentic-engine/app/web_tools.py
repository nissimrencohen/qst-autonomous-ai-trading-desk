"""Web search tools for CrewAI agents.

Returns a list of crewai-compatible tool objects. Preference order:
  1. Tavily (AGENTIC_TAVILY_API_KEY set) — higher-quality financial results
  2. DuckDuckGo — free, no key needed

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
            from crewai_tools import TavilySearchResults

            os.environ.setdefault("TAVILY_API_KEY", tavily_key)
            tool = TavilySearchResults(max_results=5)
            log.info("Web search: using Tavily")
            return [tool]
        except Exception as exc:
            log.warning("Tavily tool init failed (%s); falling back to DuckDuckGo", exc)

    try:
        from crewai_tools import DuckDuckGoSearchRun

        tool = DuckDuckGoSearchRun()
        log.info("Web search: using DuckDuckGo")
        return [tool]
    except Exception as exc:
        log.warning("DuckDuckGo tool init failed (%s); web search unavailable", exc)
        return []
