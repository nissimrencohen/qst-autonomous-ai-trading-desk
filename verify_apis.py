"""API Key Verification — 4 providers: Groq, OpenAI, GitHub Models, Tavily.

Loads root .env, makes a minimal auth call to each provider.
Hard-stops on the first authentication failure.

Usage: python verify_apis.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ── load .env ──────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv not installed.  pip install python-dotenv")
    sys.exit(1)

env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    print(f"ERROR: .env not found at {env_path}")
    sys.exit(1)

load_dotenv(env_path, override=True)

# ── helpers ────────────────────────────────────────────────────────────────────

def _mask(v: str | None) -> str:
    if not v:
        return "<not set>"
    return v[:6] + "..." + v[-3:] if len(v) > 12 else v[:4] + "..."

_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_RESET  = "\033[0m"
PASS = f"{_GREEN}PASS{_RESET}"
FAIL = f"{_RED}FAIL{_RESET}"
SKIP = f"{_YELLOW}SKIP{_RESET}"

failures: list[str] = []

def _result(label: str, ok: bool | None, detail: str = "") -> None:
    tag = PASS if ok is True else (SKIP if ok is None else FAIL)
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag}  {label}{suffix}")
    if ok is False:
        failures.append(label)

def _hard_stop(label: str, err: str) -> None:
    print(f"\n  {FAIL}  {label}")
    print(f"         Error: {err}")
    print("\n" + "=" * 60)
    print("HARD STOP — fix the key above before proceeding to Step 2.")
    print("=" * 60)
    sys.exit(1)


# ── litellm ────────────────────────────────────────────────────────────────────
try:
    import litellm
    litellm.set_verbose = False
    litellm.suppress_debug_info = True
except ImportError:
    print("ERROR: litellm not installed.  pip install litellm")
    sys.exit(1)

HELLO = [{"role": "user", "content": "Reply with exactly one word: hello"}]

print("=" * 60)
print("  Trading Desk — API Key Verification (4 providers)")
print("=" * 60)


# ── 1 / 4  Groq ───────────────────────────────────────────────────────────────
print("\n[1/4] Groq")
groq_key   = os.getenv("GROQ_API_KEY", "")
groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
print(f"      key  : {_mask(groq_key)}")
print(f"      model: {groq_model}")

if not groq_key:
    _result("Groq", None, "GROQ_API_KEY not set — skipped in router")
else:
    t0 = time.perf_counter()
    try:
        r = litellm.completion(model=f"groq/{groq_model}", messages=HELLO,
                               max_tokens=5, api_key=groq_key)
        dt = time.perf_counter() - t0
        _result("Groq", True, f"{dt:.2f}s → '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        _hard_stop("Groq", str(e))


# ── 2 / 4  OpenAI ─────────────────────────────────────────────────────────────
print("\n[2/4] OpenAI")
openai_key   = os.getenv("OPENAI_API_KEY", "")
openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
print(f"      key  : {_mask(openai_key)}")
print(f"      model: {openai_model}")

if not openai_key:
    _result("OpenAI", None, "OPENAI_API_KEY not set — skipped in router")
else:
    t0 = time.perf_counter()
    try:
        r = litellm.completion(model=openai_model, messages=HELLO,
                               max_tokens=5, api_key=openai_key)
        dt = time.perf_counter() - t0
        _result("OpenAI", True, f"{dt:.2f}s → '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        _hard_stop("OpenAI", str(e))


# ── 3 / 4  GitHub Models ──────────────────────────────────────────────────────
# Uses the OpenAI-compatible inference endpoint; auth = GitHub PAT.
print("\n[3/4] GitHub Models")
github_key      = os.getenv("GITHUB_API_KEY", "")
github_model    = os.getenv("GITHUB_MODEL", "gpt-4o-mini")
GITHUB_BASE_URL = "https://models.inference.ai.azure.com"
print(f"      key  : {_mask(github_key)}")
print(f"      model: {github_model}")
print(f"      base : {GITHUB_BASE_URL}")

if not github_key:
    _result("GitHub Models", None, "GITHUB_API_KEY not set — skipped in router")
else:
    t0 = time.perf_counter()
    try:
        r = litellm.completion(
            model=f"openai/{github_model}",
            messages=HELLO,
            max_tokens=5,
            api_key=github_key,
            base_url=GITHUB_BASE_URL,
        )
        dt = time.perf_counter() - t0
        _result("GitHub Models", True,
                f"{dt:.2f}s → '{r.choices[0].message.content.strip()}'")
    except Exception as e:
        _hard_stop("GitHub Models", str(e))


# ── 4 / 4  Tavily ─────────────────────────────────────────────────────────────
print("\n[4/4] Tavily  (web search)")
tavily_key = os.getenv("TAVILY_API_KEY", "")
print(f"      key  : {_mask(tavily_key)}")

if not tavily_key:
    _result("Tavily", None, "TAVILY_API_KEY not set — DuckDuckGo fallback will be used")
else:
    t0 = time.perf_counter()
    try:
        from tavily import TavilyClient
        result = TavilyClient(api_key=tavily_key).search("Nvidia stock", max_results=1)
        dt = time.perf_counter() - t0
        top = result.get("results", [{}])[0].get("title", "—")[:60]
        _result("Tavily", True, f"{dt:.2f}s → '{top}'")
    except ImportError:
        _result("Tavily", None,
                "tavily-python not in this env — available inside the container")
    except Exception as e:
        _hard_stop("Tavily", str(e))


# ── summary ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
if failures:
    print(f"VERIFICATION FAILED — {len(failures)} provider(s) rejected:")
    for f in failures:
        print(f"  x  {f}")
    print("Fix the key(s) above, then re-run verify_apis.py.")
    sys.exit(1)

print("ALL PROVIDERS VERIFIED (4/4) — proceeding to Step 2 (docker rebuild).")
print("=" * 60)
