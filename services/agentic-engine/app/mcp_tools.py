"""CrewAI integration for the QST MCP market-data tools.

`build_mcp_tools()` returns CrewAI-compatible tools that the desk's analysts
invoke to fetch real-time technical & fundamental data via MCP.

Two wiring modes (selected by config; both expose the SAME two tools and the
SAME payloads, since both ultimately call app.mcp_data):

  • in-process (default, AGENTIC_MCP_CREW_ADAPTER=false)
        Fast, zero-subprocess, maximally stable. The tool bodies call
        app.mcp_data directly — the identical functions the FastMCP server
        (app.mcp_server) exposes over the protocol.

  • protocol adapter (AGENTIC_MCP_CREW_ADAPTER=true)
        Spawns app.mcp_server over stdio and consumes it through
        crewai_tools.MCPServerAdapter — a genuine end-to-end MCP round-trip
        for every agent tool call. Requires the `mcpadapt` extra. Falls back
        to in-process automatically if anything goes wrong, so enabling it can
        never break a run.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from app.config import settings
from app import mcp_data

log = logging.getLogger(__name__)


def _build_inprocess_tools() -> list:
    """In-process CrewAI tools over app.mcp_data (the default, stable path)."""
    try:
        from crewai.tools import tool
    except ImportError:
        log.warning("crewai.tools unavailable — MCP tools disabled")
        return []

    @tool("get_technical_data")
    def get_technical_data(ticker: str) -> str:
        """[MCP] Live technical snapshot — current price, intraday volume, daily
        % change, RSI(14), MACD(12/26/9) with cross direction, and Bollinger
        (20,2σ) band position for a stock ticker."""
        log.info("MCP tool invoked: get_technical_data(%s)", ticker)
        return json.dumps(mcp_data.get_technical_data(ticker))

    @tool("get_fundamental_data")
    def get_fundamental_data(ticker: str) -> str:
        """[MCP] Live fundamental snapshot — P/E ratio, trailing EPS, market
        capitalisation, company name, current price and 52-week range for a
        stock ticker."""
        log.info("MCP tool invoked: get_fundamental_data(%s)", ticker)
        return json.dumps(mcp_data.get_fundamental_data(ticker))

    return [get_technical_data, get_fundamental_data]


def _build_adapter_tools() -> list:
    """Genuine MCP round-trip tools via crewai_tools.MCPServerAdapter (stdio).

    Returns [] on any failure so the caller can fall back to in-process tools.
    """
    try:
        from crewai_tools import MCPServerAdapter
        from mcp import StdioServerParameters
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP adapter unavailable (%s) — using in-process tools", exc)
        return []

    try:
        service_root = Path(__file__).resolve().parents[1]  # services/agentic-engine
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            cwd=str(service_root),
        )
        # MCPServerAdapter manages the server subprocess. We keep the adapter
        # alive for the process lifetime (module-level ref) so the tools stay
        # connected across crew runs.
        adapter = MCPServerAdapter(params)
        tools = list(adapter.tools)
        global _ADAPTER_REF
        _ADAPTER_REF = adapter  # prevent GC / premature subprocess shutdown
        log.info("MCP adapter connected — %d protocol tools: %s",
                 len(tools), [getattr(t, "name", "?") for t in tools])
        return tools
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP adapter init failed (%s) — using in-process tools", exc)
        return []


_ADAPTER_REF = None  # keeps the stdio subprocess alive when adapter mode is on


def build_mcp_tools() -> list:
    """Return the MCP market-data tools for the CrewAI desk.

    Honours AGENTIC_MCP_ENABLED (master switch) and AGENTIC_MCP_CREW_ADAPTER
    (protocol round-trip vs in-process). Always degrades gracefully to [].
    """
    if not settings.mcp_enabled:
        return []

    if settings.mcp_crew_adapter:
        tools = _build_adapter_tools()
        if tools:
            return tools
        log.info("Falling back to in-process MCP tools")

    return _build_inprocess_tools()
