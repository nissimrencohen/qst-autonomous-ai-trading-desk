import base64
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

def run_e2e_test():
    chart_path = Path("cue_chart.jpeg")
    if not chart_path.exists():
        print(f"Error: {chart_path.absolute()} not found.")
        return

    print("1. Reading and encoding chart...")
    with open(chart_path, "rb") as f:
        chart_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "ticker": "CUE",
        "question": "Provide a comprehensive technical and fundamental analysis for CUE.",
        "horizon_days": 30,
        "chart_base64": chart_b64,
        "chart_content_type": "image/jpeg"
    }

    data = json.dumps(payload).encode("utf-8")
    
    # Try the production webhook first, then test webhook
    endpoints = [
        "http://localhost:5678/webhook/analyze",
        "http://localhost:5678/webhook-test/analyze"
    ]
    
    print(f"2. Sending request to n8n orchestrator... (Payload size: {len(data)/1024:.1f} KB)")
    
    success = False
    for url in endpoints:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            t0 = time.perf_counter()
            with urllib.request.urlopen(req) as response:
                latency = time.perf_counter() - t0
                raw_resp = response.read().decode("utf-8")
                print(f"\n✅ SUCCESS! (URL: {url})")
                print(f"⏱️  Latency: {latency:.2f}s")
                print("\n--- Raw Response ---")
                print(raw_resp)
                try:
                    result = json.loads(raw_resp)
                    print("\n--- Final Probability Report ---")
                    print(json.dumps(result, indent=2))
                except json.JSONDecodeError:
                    print("\n⚠️ Response is not JSON!")
                success = True
                break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue # Try next endpoint
            print(f"❌ HTTP Error {e.code} from {url}: {e.read().decode('utf-8', errors='replace')}")
            return
        except urllib.error.URLError as e:
            print(f"❌ Network Error connecting to {url}: {e.reason}")
            return
            
    if not success:
        print("❌ Could not reach n8n webhook. Is the workflow activated?")

if __name__ == "__main__":
    run_e2e_test()
