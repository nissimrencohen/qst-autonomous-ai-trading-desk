import asyncio
import json
import urllib.request
import urllib.error
import time

PROVIDER_POOL = [
    {
        "name":         "openai",
        "target_model": "gpt-4o",
    },
    {
        "name":         "groq",
        "target_model": "groq/llama-3.1-8b-instant",
    },
    {
        "name":         "github",
        "target_model": "github/gpt-4o-mini",
    },
    {
        "name":         "gemini_flash",
        "target_model": "gemini/gemini-3.5-flash",
    },
    {
        "name":         "gemini",
        "target_model": "gemini/gemini-2.5-flash",
    },
]

async def check_model(provider_name, target_model):
    url = "http://localhost:8003/eval/synthesize"
    
    payload = {
        "ticker": "VIXY",
        "question": "Test question for connection check",
        "horizon_days": 14,
        "volatility_desk": True,
        "rag": {"summary": None, "retrieved": []},
        "eval_config": {
            "experiment_name": "connection_check",
            "run_label": f"check_{provider_name}",
            "swarm_size": "solo",
            "target_model": target_model,
            "skip_fallback": True,
        },
    }
    
    data = json.dumps(payload).encode('utf-8')
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    t0 = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        def _sync_post():
            with urllib.request.urlopen(req, timeout=30.0) as r:
                return r.status, json.loads(r.read())
        status, body = await loop.run_in_executor(None, _sync_post)
        dt = time.monotonic() - t0
        return True, status, dt, body
    except urllib.error.HTTPError as exc:
        dt = time.monotonic() - t0
        try:
            body = json.loads(exc.read())
        except Exception:
            body = {"detail": str(exc)}
        return False, exc.code, dt, body
    except Exception as exc:
        dt = time.monotonic() - t0
        return False, None, dt, {"detail": str(exc)}

async def main():
    print("Checking connection to each provider and model sequentially...\n")
    results = []
    
    for provider in PROVIDER_POOL:
        name = provider["name"]
        model = provider["target_model"]
        
        print(f"Testing provider: {name}, model: {model} ...", end="", flush=True)
        
        ok, status, dt, body = await check_model(name, model)
        
        if ok:
            print(f" OK (Status: {status}, Time: {dt:.2f}s)")
        else:
            detail = body.get("detail", str(body))
            print(f" FAILED (Status: {status}, Time: {dt:.2f}s, Error: {detail})")
            
        results.append({
            "provider": name,
            "model": model,
            "status": "OK" if ok else "FAILED",
            "http_status": status,
            "latency": f"{dt:.2f}s",
            "error": None if ok else body.get("detail", str(body))
        })
        
        # Avoid rate limits for next requests
        await asyncio.sleep(2)
        
    print("\nSummary of working models:")
    for res in results:
        if res["status"] == "OK":
            print(f" - {res['provider']} ({res['model']}) is AVAILABLE")

if __name__ == "__main__":
    asyncio.run(main())
