#!/usr/bin/env python3
"""
QST EVAL Research Lab — Phase 2 Benchmark Runner
=================================================
Autonomous test orchestrator for the swarm-size × model impact experiment.

WHAT IT DOES
------------
1. Iterates a Golden Dataset of VIX/Options/Derivatives-focused financial prompts
   against the approved watchlist tickers (VIXY, SVXY, SPCX).
2. Executes a full matrix of EvalConfig combinations:
     SwarmSize  ∈  {SOLO, TRIAD, FULL}
     Model      ∈  {gemini/gemini-2.5-flash, groq/llama-3.3-70b-versatile, gpt-4o}
3. POSTs each cell asynchronously to POST /eval/synthesize.
4. Tracks results (run_id, HTTP status, latency, error) in memory and saves
   a JSONL results log to ./data/eval_results_<timestamp>.jsonl for Phase 3.
5. Uses a per-model semaphore + inter-request jitter to avoid provider 429s.

USAGE
-----
# Run the full matrix (27 cells) against a live agentic-engine:
  python scripts/run_eval_matrix.py

# Dry-run: print the matrix without sending any requests:
  python scripts/run_eval_matrix.py --dry-run

# Limit to a specific experiment subset (fast iteration):
  python scripts/run_eval_matrix.py --swarm-sizes solo triad --models gemini/gemini-2.5-flash

# Point at a non-default engine URL:
  python scripts/run_eval_matrix.py --engine-url http://localhost:8003

REQUIREMENTS
------------
  pip install aiohttp  # async HTTP (stdlib alternative: urllib — no dep needed)

No extra dependencies beyond the standard library if AIOHTTP is unavailable —
the script falls back to a synchronous urllib implementation with the same
matrix logic and output format.

OBSERVABILITY
-------------
Every run is tagged in Langfuse + Phoenix with:
  experiment_name = "swarm_size_vs_model_impact"
  run_label       = "<swarm>_<model_safe>_<ticker>_<prompt_id>"
Traces are automatically grouped in Langfuse under the experiment tag.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Colour helpers (ANSI, degraded gracefully on Windows if needed) ───────────

_IS_TTY = sys.stdout.isatty()

# Windows: enable VT processing so ANSI codes render in modern terminals
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        _IS_TTY = False  # ANSI not supported — fall back to plain output
    # Reconfigure stdout to UTF-8 so box-drawing / emoji chars don't crash on
    # cp1252 terminals (Python 3.12+ supports reconfigure; earlier uses PYTHONIOENCODING).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")

_RESET  = "\033[0m"  if _IS_TTY else ""
_BOLD   = "\033[1m"  if _IS_TTY else ""
_DIM    = "\033[2m"  if _IS_TTY else ""
_CYAN   = "\033[36m" if _IS_TTY else ""
_GREEN  = "\033[32m" if _IS_TTY else ""
_YELLOW = "\033[33m" if _IS_TTY else ""
_RED    = "\033[31m" if _IS_TTY else ""
_MAGENTA= "\033[35m" if _IS_TTY else ""
_BLUE   = "\033[34m" if _IS_TTY else ""
_WHITE  = "\033[97m" if _IS_TTY else ""

def _ts() -> str:
    """Current UTC time as a compact HH:MM:SS string."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def _log(level: str, msg: str) -> None:
    colours = {
        "INFO":    _CYAN,
        "OK":      _GREEN,
        "WARN":    _YELLOW,
        "ERROR":   _RED,
        "RUNNING": _MAGENTA,
        "SKIP":    _DIM,
        "MATRIX":  _BLUE,
        "RESULT":  _WHITE,
        "HEADER":  _BOLD + _CYAN,
    }
    c = colours.get(level, "")
    print(f"{_DIM}{_ts()}{_RESET}  {c}{_BOLD}[{level:<7}]{_RESET}  {msg}")

# ── Golden Dataset ─────────────────────────────────────────────────────────────
# 5 research-grade prompts focused on VIX, options flow, and derivatives.
# All tickers are on the approved watchlist (VIXY, SVXY, SPCX, NVDA, AAPL).
# Each prompt is designed to stress-test the agent's ability to reason about
# fear-index dynamics, term-structure, and derivative positioning.

@dataclass(frozen=True)
class Prompt:
    id: str              # short slug used in run_label / JSONL key
    ticker: str          # must be on the approved watchlist
    question: str        # the analyst question
    horizon_days: int    # evaluation horizon
    volatility_desk: bool = True   # route as a vol-desk request

GOLDEN_DATASET: list[Prompt] = [
    Prompt(
        id="vix_regime_vixy",
        ticker="VIXY",
        question=(
            "The VIX front-month futures are in steep backwardation while spot VIX "
            "sits above 28. Is VIXY's roll yield turning positive, and what does the "
            "current term-structure imply for a 14-day holding period given that SPX "
            "3-month realised vol is running near 22%?"
        ),
        horizon_days=14,
    ),
    Prompt(
        id="contango_decay_svxy",
        ticker="SVXY",
        question=(
            "SVXY has rallied 18% in 30 days as VIX normalised from a fear spike. "
            "With the VIX curve back in contango and the 9-day/30-day spread at -2.5 "
            "volatility points, what is the risk of an inverse-volatility unwind if "
            "the S&P 500 drops more than 5% intraday?"
        ),
        horizon_days=30,
    ),
    Prompt(
        id="options_flow_vixy",
        ticker="VIXY",
        question=(
            "Unusual put/call activity on VIXY shows a 0.4 put/call ratio with "
            "elevated IV skew on the 30-day 25-delta put. Does the options market "
            "imply that institutional desks are hedging long-vol exposure ahead of "
            "a macro event, or is this gamma-squeeze risk on a short-squeeze scenario?"
        ),
        horizon_days=7,
    ),
    Prompt(
        id="spcx_launch_vol",
        ticker="SPCX",
        question=(
            "SPCX (SpaceX, Nasdaq IPO June 2026) has a 90-day lock-up expiry approaching. "
            "How does the combination of a binary lock-up catalyst, a freshly-IPO'd "
            "options chain with wide bid-ask spreads, and a Starship launch scheduled "
            "within the horizon affect the risk assessment and max position size?"
        ),
        horizon_days=30,
        volatility_desk=False,  # space-economy desk leads here
    ),
    Prompt(
        id="fear_spike_cross_asset",
        ticker="SVXY",
        question=(
            "A geopolitical shock has pushed VIX to 38 intraday and the VIX term "
            "structure has snapped into extreme backwardation (9D VIX 42, 30D VIX 35, "
            "3M VIX 27). At what term-structure normalisation threshold should a "
            "mean-reversion long on SVXY be considered, and what is the historical "
            "max drawdown of SVXY during a vol spike of this magnitude?"
        ),
        horizon_days=21,
    ),
]

# ── Experiment Matrix Definition ──────────────────────────────────────────────
# Axis 1 — SwarmSize: SOLO (1 agent), TRIAD (3 agents), FULL (7 agents)
# Axis 2 — Models:    labelled model configs. Each cell tries target_model first;
#                     on failure (429/502/auth), the runner falls back through
#                     FALLBACK_CHAIN until one succeeds or all are exhausted.
#
# Total cells = 3 swarm sizes × 3 models × 5 prompts = 45 runs.
# Execution is STRICTLY SERIAL — one request at a time globally — to never
# trigger rate-limits on any free-tier provider.

SWARM_SIZES: list[str] = ["solo", "triad", "full"]

# All available providers — matches RAG_LLM_PROVIDER_CHAIN order exactly:
#   openai → groq → github → gemini_flash → gemini
# NO local models (Ollama). Strictly cloud-only providers.
# Each cell tries its primary first, then falls back through this pool in order.
PROVIDER_POOL: list[dict[str, Any]] = [
    {
        "name":         "openai",
        "target_model": "gpt-4o",
        "delay_s":      5.0,     # freellmapi proxy — moderate pacing
    },
    {
        "name":         "groq",
        "target_model": "groq/llama-3.1-8b-instant",
        "delay_s":      10.0,    # Groq: 6000 TPM, need spacing
    },
    {
        "name":         "github",
        "target_model": "github/gpt-4o-mini",
        "delay_s":      5.0,     # GitHub Models: generous free tier
    },
    {
        "name":         "gemini_flash",
        "target_model": "gemini/gemini-3.5-flash",
        "delay_s":      8.0,     # GA model, higher rate limits than primary
    },
    {
        "name":         "gemini",
        "target_model": "gemini/gemini-2.5-flash",
        "delay_s":      8.0,     # Gemini free tier: 15 RPM
    },
]

# The 3 "labelled" model dimensions the dashboard expects.
# Each gets a primary provider and will try the full PROVIDER_POOL as fallback.
MODELS: list[dict[str, Any]] = [
    {
        "label":        "gemini-2.5-flash",
        "primary":      "gemini",         # try gemini first for this label
        "concurrency":  1,
        "delay_s":      8.0,
    },
    {
        "label":        "llama-3.1-8b",
        "primary":      "groq",           # try groq first
        "concurrency":  1,
        "delay_s":      10.0,
    },
    {
        "label":        "gpt-4o",
        "primary":      "openai",         # try openai (freellmapi) first
        "concurrency":  1,
        "delay_s":      5.0,
    },
]

# Build a lookup: provider name → target_model string
_PROVIDER_MAP: dict[str, dict] = {p["name"]: p for p in PROVIDER_POOL}


def _build_fallback_chain(primary_name: str) -> list[dict]:
    """Return an ordered list: [primary_provider, ...rest of pool]."""
    primary = _PROVIDER_MAP.get(primary_name)
    rest = [p for p in PROVIDER_POOL if p["name"] != primary_name]
    if primary:
        return [primary] + rest
    return rest  # unknown primary → try all


EXPERIMENT_NAME = "swarm_size_vs_model_impact"

# ── Result tracking ────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    run_label:       str
    experiment_name: str
    ticker:          str
    prompt_id:       str
    swarm_size:      str
    target_model:    str | None
    model_label:     str
    status:          str   # "ok" | "error" | "skipped" | "dry_run"
    http_status:     int | None = None
    run_id:          str | None = None
    latency_s:       float | None = None
    error:           str | None = None
    started_at:      str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    bullish:         float | None = None
    confidence:      float | None = None
    risk_level:      str | None = None

# ── HTTP Layer (aiohttp preferred, urllib fallback) ───────────────────────────

async def _post_eval(
    engine_url: str,
    prompt: Prompt,
    swarm_size: str,
    target_model: str | None,
    experiment_name: str,
    run_label: str,
    timeout_s: float = 360.0,
) -> dict[str, Any]:
    """Send one POST /eval/synthesize request. Returns the parsed JSON body."""
    payload = {
        "ticker": prompt.ticker,
        "question": prompt.question,
        "horizon_days": prompt.horizon_days,
        "volatility_desk": prompt.volatility_desk,
        "rag": {"summary": None, "retrieved": []},
        "eval_config": {
            "experiment_name": experiment_name,
            "run_label": run_label,
            "swarm_size": swarm_size,
            "target_model": target_model,
            "skip_fallback": True,
        },
    }

    url = f"{engine_url.rstrip('/')}/eval/synthesize"
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    # Try aiohttp first (best async experience)
    try:
        import aiohttp  # type: ignore[import]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                body = await resp.json(content_type=None)
                body["__http_status"] = resp.status
                return body
    except ImportError:
        pass  # fall through to urllib

    # Stdlib urllib fallback (synchronous — run in a thread)
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        loop = asyncio.get_event_loop()
        def _sync_post():
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                body = json.loads(r.read())
                body["__http_status"] = r.status
                return body
        return await loop.run_in_executor(None, _sync_post)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {"detail": str(exc)}
        body["__http_status"] = exc.code
        return body


async def _run_cell_with_fallback(
    engine_url: str,
    prompt: Prompt,
    swarm_size: str,
    model: dict[str, Any],
    cell_index: int,
    total_cells: int,
    dry_run: bool,
) -> RunResult:
    """Execute one matrix cell with provider fallback.

    Tries the primary provider first, then falls through the PROVIDER_POOL
    until one succeeds or all are exhausted. Each provider attempt gets
    exactly ONE shot (no intra-provider retries) to keep the wall-clock
    time bounded and avoid hammering a broken provider.
    """
    model_label  = model["label"]
    primary_name = model.get("primary", PROVIDER_POOL[0]["name"])
    chain        = _build_fallback_chain(primary_name)

    safe_model = model_label.replace("/", "-").replace(":", "-")
    run_label  = f"{swarm_size}_{safe_model}_{prompt.ticker.lower()}_{prompt.id}"

    result = RunResult(
        run_label=run_label,
        experiment_name=EXPERIMENT_NAME,
        ticker=prompt.ticker,
        prompt_id=prompt.id,
        swarm_size=swarm_size,
        target_model=None,          # filled on success
        model_label=model_label,
        status="pending",
    )

    cell_tag = (
        f"{_BOLD}{prompt.ticker:<5}{_RESET} "
        f"| {_YELLOW}{swarm_size.upper():<5}{_RESET} "
        f"| {_CYAN}{model_label:<22}{_RESET} "
        f"| {_DIM}{prompt.id}{_RESET}"
    )
    progress = f"{_DIM}({cell_index}/{total_cells}){_RESET}"

    if dry_run:
        _log("SKIP", f"{progress} DRY-RUN {cell_tag}")
        result.status = "dry_run"
        return result

    _log("RUNNING", f"{progress} {cell_tag}")
    t0 = time.monotonic()
    last_error = "all providers exhausted"

    for provider in chain:
        prov_name    = provider["name"]
        target_model = provider["target_model"]
        delay_s      = provider["delay_s"]

        try:
            resp = await _post_eval(
                engine_url=engine_url,
                prompt=prompt,
                swarm_size=swarm_size,
                target_model=target_model,
                experiment_name=EXPERIMENT_NAME,
                run_label=run_label,
            )
            http_status = resp.get("__http_status")

            if http_status in (200, 201):
                elapsed = round(time.monotonic() - t0, 2)
                resp.pop("__http_status", None)
                result.status       = "ok"
                result.http_status  = http_status
                result.latency_s    = elapsed
                result.target_model = target_model
                result.run_id       = resp.get("run_id")
                probs               = resp.get("probabilities", {})
                result.bullish      = probs.get("bullish")
                result.confidence   = resp.get("confidence")
                result.risk_level   = (resp.get("risk_assessment") or {}).get("risk_level")
                _log(
                    "OK",
                    f"{progress} {cell_tag}  "
                    f"{_GREEN}{elapsed:.1f}s{_RESET} via {_CYAN}{prov_name}{_RESET}  "
                    f"bull={_BOLD}{result.bullish or '?'}{_RESET}  "
                    f"risk={result.risk_level or '?'}  "
                    f"run_id={_DIM}{result.run_id or '?'}{_RESET}",
                )
                # Delay AFTER success so we don't burst the next cell
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                return result

            # Non-success — log and try next provider
            detail = resp.get("detail", json.dumps(resp)[:120])
            last_error = f"{prov_name}→HTTP {http_status}: {detail}"
            _log(
                "WARN",
                f"{progress} {cell_tag}  {_YELLOW}{prov_name}→{http_status}: {detail[:80]}. "
                f"Trying next provider…{_RESET}",
            )
            # Small backoff between provider attempts
            await asyncio.sleep(3.0)

        except Exception as exc:
            last_error = f"{prov_name}→EXC: {exc}"
            _log(
                "WARN",
                f"{progress} {cell_tag}  {_YELLOW}{prov_name} exception: {str(exc)[:80]}. "
                f"Trying next provider…{_RESET}",
            )
            await asyncio.sleep(3.0)

    # All providers exhausted
    elapsed = round(time.monotonic() - t0, 2)
    result.status     = "error"
    result.latency_s  = elapsed
    result.error      = last_error
    _log("ERROR", f"{progress} {cell_tag}  {_RED}ALL PROVIDERS FAILED: {last_error[:100]}{_RESET}")
    return result


# ── Results persistence ────────────────────────────────────────────────────────

def _save_results(results: list[RunResult], out_dir: Path) -> Path:
    """Write results as JSONL (one record per line) for Phase 3 aggregation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = out_dir / f"eval_results_{ts}.jsonl"
    with fname.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), default=str) + "\n")
    return fname


def _print_summary(results: list[RunResult]) -> None:
    """Print a colour-coded summary table after all cells complete."""
    ok      = [r for r in results if r.status == "ok"]
    errors  = [r for r in results if r.status == "error"]
    skipped = [r for r in results if r.status in ("dry_run", "skipped")]

    total   = len(results)
    print()
    print(f"{_BOLD}{_CYAN}{'-'*72}{_RESET}")
    print(f"{_BOLD}  EVAL MATRIX COMPLETE{_RESET}   "
          f"{_GREEN}{len(ok)} OK{_RESET}  |  "
          f"{_RED}{len(errors)} ERR{_RESET}  |  "
          f"{_DIM}{len(skipped)} SKIP{_RESET}  "
          f"|  {total} total")
    print(f"{_BOLD}{_CYAN}{'-'*72}{_RESET}")

    if ok:
        print(f"\n{_BOLD}  Successful runs:{_RESET}")
        # Group by swarm_size × model_label for the summary
        from itertools import groupby
        def _key(r): return (r.swarm_size, r.model_label)
        sorted_ok = sorted(ok, key=_key)
        for (sw, ml), group in groupby(sorted_ok, key=_key):
            runs = list(group)
            avg_lat  = sum(r.latency_s or 0 for r in runs) / len(runs)
            avg_bull = sum(r.bullish or 0 for r in runs) / len(runs)
            print(
                f"    {_YELLOW}{sw.upper():<6}{_RESET} "
                f"| {_CYAN}{ml:<24}{_RESET} "
                f"| n={len(runs)}  "
                f"avg_lat={avg_lat:.1f}s  "
                f"avg_bull={avg_bull:.2f}"
            )

    if errors:
        print(f"\n{_BOLD}  {_RED}Failed runs:{_RESET}")
        for r in errors:
            print(f"    {_RED}✗{_RESET} {r.run_label}  → {r.error or 'unknown'}")

    print(f"\n{_DIM}  Langfuse experiment tag:  {EXPERIMENT_NAME}{_RESET}")
    print(f"{_DIM}  Phoenix experiment field: eval_experiment={EXPERIMENT_NAME}{_RESET}")
    print()


# ── CLI / Entry-point ─────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QST EVAL Research Lab — Phase 2 Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--engine-url",
        default=os.getenv("AGENTIC_ENGINE_URL", "http://localhost:8003"),
        help="Base URL of the agentic-engine service (default: http://localhost:8003)",
    )
    p.add_argument(
        "--swarm-sizes",
        nargs="+",
        choices=["solo", "triad", "full"],
        default=SWARM_SIZES,
        metavar="SIZE",
        help="Swarm sizes to include in the matrix (default: solo triad full)",
    )
    p.add_argument(
        "--models",
        nargs="+",
        default=[m["label"] for m in MODELS],
        metavar="MODEL",
        help="Model labels to include (default: all three: gemini-2.5-flash llama-3.1-8b gpt-4o)",
    )
    p.add_argument(
        "--prompt-ids",
        nargs="+",
        default=None,
        metavar="ID",
        help="Run only specific Golden Dataset prompt IDs (e.g. vix_regime_vixy)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the full matrix without sending any HTTP requests",
    )
    p.add_argument(
        "--output-dir",
        default="./data",
        help="Directory to write the JSONL results file (default: ./data)",
    )

    return p.parse_args()


async def _main(args: argparse.Namespace) -> int:
    # ── Print banner ───────────────────────────────────────────────────────────
    print()
    print(f"{_BOLD}{_CYAN}{'='*72}{_RESET}")
    print(f"{_BOLD}  QST EVAL RESEARCH LAB  |  Phase 2 Benchmark Runner{_RESET}")
    print(f"{_BOLD}  Mode: RESILIENT SERIAL w/ Provider Fallback{_RESET}")
    print(f"{_BOLD}{_CYAN}{'='*72}{_RESET}")
    print(f"  Engine URL    : {args.engine_url}")
    print(f"  Experiment    : {EXPERIMENT_NAME}")
    print(f"  Swarm sizes   : {args.swarm_sizes}")
    print(f"  Model labels  : {[m['label'] for m in MODELS]}")
    print(f"  Provider pool : {[p['name'] for p in PROVIDER_POOL]}")
    print(f"  Dry run       : {args.dry_run}")
    print(f"{_BOLD}{_CYAN}{'─'*72}{_RESET}")
    print()

    # ── Filter dataset per CLI args ────────────────────────────────────────────
    active_prompts = [
        p for p in GOLDEN_DATASET
        if args.prompt_ids is None or p.id in args.prompt_ids
    ]
    # Filter models by label (not by target_model — labels are stable)
    if args.models:
        active_models = [m for m in MODELS if m["label"] in args.models]
    else:
        active_models = list(MODELS)

    if not active_prompts:
        _log("ERROR", "No prompts matched the --prompt-ids filter. Check your IDs.")
        return 1
    if not active_models:
        _log("ERROR", "No models matched the --models filter. Check model labels.")
        return 1

    # ── Build the full ordered matrix ──────────────────────────────────────────
    # Interleave models so we spread provider load: prompt → swarm → model.
    # This means consecutive cells hit DIFFERENT providers, giving each one
    # breathing room between requests.
    cells: list[tuple[Prompt, str, dict]] = []
    for prompt in active_prompts:
        for swarm in args.swarm_sizes:
            for model in active_models:
                cells.append((prompt, swarm, model))

    total = len(cells)
    _log(
        "MATRIX",
        f"{_BOLD}{total} cells{_RESET}  "
        f"({len(active_prompts)} prompts × {len(args.swarm_sizes)} swarms × {len(active_models)} models)",
    )
    print()

    # ── Print the full matrix before starting ──────────────────────────────────
    _log("MATRIX", "Execution plan:")
    for i, (prompt, swarm, model) in enumerate(cells, 1):
        chain = _build_fallback_chain(model.get("primary", ""))
        chain_str = " → ".join(p["name"] for p in chain)
        _log(
            "MATRIX",
            f"  {_DIM}{i:>3}{_RESET} "
            f"{_BOLD}{prompt.ticker:<5}{_RESET} "
            f"| {_YELLOW}{swarm.upper():<5}{_RESET} "
            f"| {_CYAN}{model['label']:<22}{_RESET} "
            f"| {_DIM}[{chain_str}]{_RESET} "
            f"| {prompt.id}",
        )
    print()

    if args.dry_run:
        _log("SKIP", "Dry-run mode — no HTTP requests will be sent.")
        return 0

    # ── Health-check the engine before starting ────────────────────────────────
    _log("INFO", f"Health-checking {args.engine_url}/health …")
    try:
        import urllib.request
        with urllib.request.urlopen(f"{args.engine_url}/health", timeout=5) as r:
            hc = json.loads(r.read())
            _log("OK", f"Engine alive: {hc.get('service')} v{hc.get('version')} uptime={hc.get('uptime_s')}s")
    except Exception as exc:
        _log("WARN", f"Health check failed ({exc}). Proceeding anyway — engine may still be starting.")
    print()

    # ── STRICTLY SERIAL execution ──────────────────────────────────────────────
    # Run cells ONE AT A TIME. This is the safest possible approach to avoid
    # any rate limit issues: no provider ever sees concurrent requests.
    # Each cell independently falls back through the full provider chain.

    wall_t0 = time.monotonic()
    results: list[RunResult] = []

    for i, (prompt, swarm, model) in enumerate(cells, 1):
        result = await _run_cell_with_fallback(
            engine_url=args.engine_url,
            prompt=prompt,
            swarm_size=swarm,
            model=model,
            cell_index=i,
            total_cells=total,
            dry_run=False,
        )
        results.append(result)

    wall_elapsed = round(time.monotonic() - wall_t0, 1)

    _log("INFO", f"All {total} cells completed in {wall_elapsed}s wall-clock time.")

    # ── Save JSONL results for Phase 3 ─────────────────────────────────────────
    out_file = _save_results(results, Path(args.output_dir))
    _log("OK", f"Results written → {out_file}")

    # ── Summary table ──────────────────────────────────────────────────────────
    _print_summary(results)

    error_count = sum(1 for r in results if r.status == "error")
    return 1 if error_count > 0 else 0


def main() -> None:

    args = _parse_args()

    # Structured logging for the runner itself (separate from engine logs)
    logging.basicConfig(
        level=logging.WARNING,  # suppress aiohttp/urllib noise
        format="%(levelname)s  %(name)s  %(message)s",
    )

    try:
        exit_code = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Interrupted — partial results may have been saved.{_RESET}")
        exit_code = 130

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
