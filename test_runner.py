import os
import sys

# Set environment variables for tests
os.environ["PYTHONPATH"] = r"C:\Autonomous AI Trading Desk\services\agentic-engine"
os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"
os.environ["AGENTIC_LLM_PROVIDER"] = "openai"

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        print("Running live test...")
        sys.path.insert(0, r"C:\Autonomous AI Trading Desk\services\agentic-engine")
        from scripts.test_ingestion_live import run_live_test
        import asyncio
        asyncio.run(run_live_test())
    else:
        print("Running synthesize tests...")
        import pytest
        sys.exit(pytest.main(["services/agentic-engine/tests/test_synthesize.py", "-v", "-p", "no:cacheprovider"]))
