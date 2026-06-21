"""QST Market-Data MCP Server (Model Context Protocol).

A standards-compliant MCP server exposing live technical and fundamental
market-data tools. Because it speaks MCP, ANY MCP client can consume it —
Claude Desktop, IDE agents, the CrewAI `MCPServerAdapter`, or the isolated
test harness (`test_mcp_data.py`).

Tools
─────
  get_technical_data(ticker)    → price, volume, %chg, RSI, MACD, Bollinger
  get_fundamental_data(ticker)  → P/E, EPS, market cap, name, 52w range

Run (stdio — the default MCP transport):
    python -m app.mcp_server

Register in an MCP client (e.g. Claude Desktop config):
    {
      "mcpServers": {
        "qst-market-data": {
          "command": "python",
          "args": ["-m", "app.mcp_server"],
          "cwd": "services/agentic-engine"
        }
      }
    }

The tool bodies delegate to app.mcp_data so the protocol path and the
in-process CrewAI path (app.mcp_tools) return identical payloads.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from app import mcp_data

log = logging.getLogger(__name__)

SERVER_NAME = "qst-market-data"

mcp = FastMCP(SERVER_NAME)


@mcp.tool()
def get_technical_data(ticker: str) -> dict[str, Any]:
    """Live technical snapshot for a stock ticker.

    Returns current price, intraday volume, daily % change, RSI(14),
    MACD(12/26/9) with cross direction, and Bollinger(20,2σ) band position.

    Args:
        ticker: Stock symbol, e.g. "AAPL" or "NVDA".
    """
    return mcp_data.get_technical_data(ticker)


@mcp.tool()
def get_fundamental_data(ticker: str) -> dict[str, Any]:
    """Live fundamental snapshot for a stock ticker.

    Returns P/E ratio, trailing EPS, market capitalisation, company name,
    current price, 52-week range, and currency.

    Args:
        ticker: Stock symbol, e.g. "AAPL" or "NVDA".
    """
    return mcp_data.get_fundamental_data(ticker)


def main(argv: list[str] | None = None) -> None:
    """Entry point. Defaults to stdio; pass `--transport sse` for HTTP/SSE."""
    argv = argv if argv is not None else sys.argv[1:]
    transport = "stdio"
    if "--transport" in argv:
        try:
            transport = argv[argv.index("--transport") + 1]
        except IndexError:
            transport = "stdio"
    log.info("Starting %s MCP server (transport=%s)", SERVER_NAME, transport)
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
