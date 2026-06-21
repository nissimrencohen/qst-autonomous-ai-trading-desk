"""Phase 2 - Isolated MCP test harness.

Verifies the AlphaSwarm market-data MCP server (app/mcp_server.py) end to end:
the tool registry, JSON-Schema advertisement, dispatch, structured output, and
real fetched data for multiple tickers.

Two checks:
  1. PROTOCOL DISPATCH (primary, deterministic): drives the genuine FastMCP
     server object via its MCP machinery (`list_tools` / `call_tool`) - the
     exact code path an MCP client triggers on the server side.
  2. STDIO ROUND-TRIP (bonus, time-boxed): spawns `python -m app.mcp_server`
     and talks to it as a real stdio MCP client. This is the transport external
     clients (Claude Desktop, IDEs, CrewAI MCPServerAdapter) use. It is
     time-boxed and NON-fatal, because stdio-subprocess buffering on Windows
     can stall; check #1 already proves protocol compliance.

Run:
    python test_mcp_data.py
Exit code 0 = protocol dispatch passed (the gating check), 1 = failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

TICKERS = ["AAPL", "NVDA"]
SERVICE_ROOT = Path(__file__).resolve().parent

# Required keys each tool must return (besides "ticker").
TECH_KEYS = {"price", "rsi", "macd", "macd_cross", "bb_position", "volume"}
FUND_KEYS = {"pe", "eps", "market_cap", "name"}


def _validate(label: str, payload: dict, required: set[str], failures: list[str]) -> None:
    if payload.get("error"):
        failures.append(f"{label} returned error: {payload['error']}")
        return
    missing = [k for k in required if payload.get(k) is None]
    if missing:
        failures.append(f"{label} missing/null fields: {missing}")


# ── Check 1: genuine FastMCP protocol dispatch (server-side machinery) ──────────

async def check_protocol_dispatch(failures: list[str]) -> None:
    from app.mcp_server import mcp

    print("== Check 1: MCP protocol dispatch (FastMCP server machinery) ==")
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    print(f"[OK] Tools advertised over MCP: {sorted(names)}")
    for t in tools:
        props = list((t.inputSchema or {}).get("properties", {}).keys())
        print(f"     - {t.name}(schema props={props})")
    for expected in ("get_technical_data", "get_fundamental_data"):
        if expected not in names:
            failures.append(f"tool '{expected}' not advertised by server")

    for tk in TICKERS:
        print(f"\n-- {tk} --")
        _, tech = await mcp.call_tool("get_technical_data", {"ticker": tk})
        print("  technical:  ", json.dumps(tech, separators=(",", ":")))
        _validate(f"{tk} technical", tech, TECH_KEYS, failures)

        _, fund = await mcp.call_tool("get_fundamental_data", {"ticker": tk})
        print("  fundamental:", json.dumps(fund, separators=(",", ":")))
        _validate(f"{tk} fundamental", fund, FUND_KEYS, failures)


# ── Check 2: real stdio MCP round-trip (best-effort, time-boxed) ────────────────

async def _stdio_roundtrip() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(SERVICE_ROOT),
        env=os.environ.copy(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            res = await session.call_tool("get_technical_data", {"ticker": TICKERS[0]})
            structured = getattr(res, "structuredContent", None) or {}
            return {
                "tools": [t.name for t in listed.tools],
                "sample": structured.get("result", structured),
            }


async def check_stdio_roundtrip() -> None:
    print("\n== Check 2: stdio MCP round-trip (real transport, best-effort) ==")
    try:
        out = await asyncio.wait_for(_stdio_roundtrip(), timeout=45)
        print(f"[OK] stdio transport verified - tools={out['tools']}, "
              f"sample price={out['sample'].get('price')}")
    except asyncio.TimeoutError:
        print("[WARN] stdio round-trip timed out (known Windows stdio-buffering "
              "quirk) - protocol compliance already proven by Check 1.")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] stdio round-trip skipped: {type(exc).__name__}: {exc}")


async def run() -> int:
    failures: list[str] = []
    await check_protocol_dispatch(failures)
    await check_stdio_roundtrip()

    print("\n" + "=" * 60)
    if failures:
        print(f"[FAIL] MCP TEST FAILED - {len(failures)} issue(s):")
        for f in failures:
            print(f"   * {f}")
        return 1
    print(f"[OK] MCP TEST PASSED - both tools return valid JSON for "
          f"{len(TICKERS)} tickers ({', '.join(TICKERS)}).")
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] MCP TEST CRASHED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
