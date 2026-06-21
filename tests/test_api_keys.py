"""
Test all API keys configured in the .env file.
Runs LIVE calls against each provider to verify keys are valid and active.
"""
import os
import sys
import time

# Load the root .env file
env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_file):
    with open(env_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

results = []


def ok(name, detail=""):
    msg = f"{GREEN}{BOLD}[PASS]{RESET} {name}"
    if detail:
        msg += f"  →  {detail}"
    print(msg)
    results.append((name, True))


def fail(name, detail=""):
    msg = f"{RED}{BOLD}[FAIL]{RESET} {name}"
    if detail:
        msg += f"  →  {detail}"
    print(msg)
    results.append((name, False))


def section(title):
    print(f"\n{BOLD}{YELLOW}{'─'*55}{RESET}")
    print(f"{BOLD}{YELLOW}  {title}{RESET}")
    print(f"{BOLD}{YELLOW}{'─'*55}{RESET}")


# ── 1. GROQ ────────────────────────────────────────────────
section("1. Groq (llama-3.3-70b-versatile)")
try:
    import litellm
    key = os.environ.get("AGENTIC_GROQ_API_KEY", "")
    if not key:
        fail("Groq", "AGENTIC_GROQ_API_KEY not set")
    else:
        resp = litellm.completion(
            model="groq/llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Reply with exactly: GROQ_OK"}],
            api_key=key,
            max_tokens=10,
        )
        content = resp.choices[0].message.content.strip()
        ok("Groq", f"response='{content}'")
except Exception as e:
    fail("Groq", str(e)[:120])

# ── 2. OPENAI ──────────────────────────────────────────────
section("2. OpenAI (gpt-4o-mini)")
try:
    key = os.environ.get("AGENTIC_OPENAI_API_KEY", "")
    if not key:
        fail("OpenAI", "AGENTIC_OPENAI_API_KEY not set")
    else:
        resp = litellm.completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Reply with exactly: OPENAI_OK"}],
            api_key=key,
            max_tokens=10,
        )
        content = resp.choices[0].message.content.strip()
        ok("OpenAI", f"response='{content}'")
except Exception as e:
    fail("OpenAI", str(e)[:120])

# ── 3. GEMINI ──────────────────────────────────────────────
section("3. Google Gemini (gemini-2.5-flash)")
try:
    key = os.environ.get("AGENTIC_GOOGLE_API_KEY", "")
    if not key:
        fail("Gemini", "AGENTIC_GOOGLE_API_KEY not set")
    else:
        resp = litellm.completion(
            model="gemini/gemini-2.5-flash",
            messages=[{"role": "user", "content": "Reply with exactly: GEMINI_OK"}],
            api_key=key,
            max_tokens=50,
            thinking={"type": "disabled"},
        )
        # gemini-2.5-flash may return thinking tokens; find first non-None content
        content = None
        for choice in resp.choices:
            c = choice.message.content
            if c is not None:
                content = c.strip()
                break
        if content:
            ok("Gemini", f"response='{content[:40]}'")
        else:
            fail("Gemini", "Empty response from Gemini")
except Exception as e:
    fail("Gemini", str(e)[:120])

# ── 4. GITHUB MODELS ───────────────────────────────────────
section("4. GitHub Models (gpt-4o-mini)")
try:
    key = os.environ.get("AGENTIC_GITHUB_API_KEY", "")
    if not key:
        fail("GitHub Models", "AGENTIC_GITHUB_API_KEY not set")
    else:
        resp = litellm.completion(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "Reply with exactly: GITHUB_OK"}],
            api_key=key,
            api_base="https://models.inference.ai.azure.com",
            max_tokens=10,
        )
        content = resp.choices[0].message.content.strip()
        ok("GitHub Models", f"response='{content}'")
except Exception as e:
    fail("GitHub Models", str(e)[:120])

# ── 5. TAVILY ──────────────────────────────────────────────
section("5. Tavily Search API")
try:
    import httpx
    key = os.environ.get("AGENTIC_TAVILY_API_KEY", "")
    if not key:
        fail("Tavily", "AGENTIC_TAVILY_API_KEY not set")
    else:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"query": "GOOGL stock price", "max_results": 1},
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            ok("Tavily", f"HTTP {resp.status_code} — search returned {len(resp.json().get('results', []))} results")
        else:
            fail("Tavily", f"HTTP {resp.status_code}: {resp.text[:80]}")
except Exception as e:
    fail("Tavily", str(e)[:120])

# ── 6. HELICONE ────────────────────────────────────────────
section("6. Helicone Proxy (via Groq)")
try:
    groq_key   = os.environ.get("AGENTIC_GROQ_API_KEY", "")
    heli_key   = os.environ.get("HELICONE_API_KEY", "")
    if not heli_key:
        fail("Helicone", "HELICONE_API_KEY not set")
    elif not groq_key:
        fail("Helicone", "Needs GROQ key to proxy through Helicone")
    else:
        resp = litellm.completion(
            model="groq/llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Reply with exactly: HELICONE_OK"}],
            api_key=groq_key,
            api_base="https://groq.helicone.ai/openai/v1",
            extra_headers={"Helicone-Auth": f"Bearer {heli_key}"},
            max_tokens=10,
        )
        content = resp.choices[0].message.content.strip()
        ok("Helicone", f"proxied Groq response='{content}'")
except Exception as e:
    fail("Helicone", str(e)[:120])

# ── 7. YFINANCE ────────────────────────────────────────────
section("7. yfinance (no key — sanity check)")
try:
    import yfinance as yf
    tk = yf.Ticker("GOOGL")
    fi = tk.fast_info
    # last_price may be None outside market hours; fall back to previousClose
    price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
    if not price:
        info = tk.info
        price = info.get("regularMarketPrice") or info.get("previousClose")
    if price:
        ok("yfinance", f"GOOGL price={price}")
    else:
        fail("yfinance", "No price returned for GOOGL")
except Exception as e:
    fail("yfinance", str(e)[:120])

# ── SUMMARY ────────────────────────────────────────────────
total = len(results)
passed = sum(1 for _, v in results if v)
failed = total - passed

print(f"\n{BOLD}{'═'*55}{RESET}")
print(f"{BOLD}  RESULTS: {passed}/{total} APIs operational{RESET}")
print(f"{BOLD}{'═'*55}{RESET}")
for name, status in results:
    icon = f"{GREEN}✓{RESET}" if status else f"{RED}✗{RESET}"
    print(f"  {icon}  {name}")
print()

sys.exit(0 if failed == 0 else 1)
