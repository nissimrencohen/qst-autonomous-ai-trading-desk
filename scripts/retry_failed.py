import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
import time
import sys

# Import necessary components from run_eval_matrix
import run_eval_matrix as rem

async def main():
    data_dir = Path("./data")
    # Find latest jsonl
    candidates = sorted(data_dir.glob("eval_results_*.jsonl"), reverse=True)
    if not candidates:
        print("No results found.")
        return
    
    latest_file = candidates[0]
    print(f"Reading from {latest_file}")
    
    # Read all lines
    records = []
    with open(latest_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line.strip()))
                
    failed_indices = [i for i, r in enumerate(records) if r.get("status") == "error"]
    
    print(f"Found {len(failed_indices)} failed runs out of {len(records)}.")
    
    if not failed_indices:
        print("No failed runs to retry.")
        return
        
    engine_url = "http://localhost:8003"
    
    # Build maps for lookup
    prompt_map = {p.id: p for p in rem.GOLDEN_DATASET}
    model_map = {m["label"]: m for m in rem.MODELS}
    
    print("Retrying failed runs...")
    
    for count, idx in enumerate(failed_indices, 1):
        rec = records[idx]
        prompt = prompt_map[rec["prompt_id"]]
        model = model_map[rec["model_label"]]
        swarm = rec["swarm_size"]
        
        print(f"\nRetrying {count}/{len(failed_indices)}: {prompt.ticker} | {swarm} | {model['label']}")
        
        # We invoke _run_cell_with_fallback directly
        result = await rem._run_cell_with_fallback(
            engine_url=engine_url,
            prompt=prompt,
            swarm_size=swarm,
            model=model,
            cell_index=count,
            total_cells=len(failed_indices),
            dry_run=False,
        )
        
        # Replace the record with the new result
        import dataclasses
        records[idx] = dataclasses.asdict(result)
        
        # Optional: wait a bit between retries
        await asyncio.sleep(2)
        
    # Write a new jsonl file with the updated records
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = data_dir / f"eval_results_{ts}.jsonl"
    
    with open(out_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
            
    print(f"\nSaved updated results to {out_file}")

if __name__ == "__main__":
    asyncio.run(main())
