"""
Run the full test suite across all 4 services.

Each service runs in an isolated subprocess so the shared `app` package name
doesn't pollute sys.modules across services.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

SERVICES = [
    "services/agentic-engine",
    "services/guardrails-service",
    "services/rag-service",
    "services/vision-analyser",
]


def run_service(service_path: str) -> tuple[str, int, str]:
    result = subprocess.run(
        [PYTHON, "-m", "pytest", "tests/", "-v", "--tb=short", "--no-header"],
        cwd=REPO_ROOT / service_path,
        capture_output=True,
        text=True,
    )
    return service_path, result.returncode, result.stdout + result.stderr


def main() -> None:
    print("=" * 70)
    print("Trading Desk — Full Test Suite")
    print("=" * 70)

    total_passed = total_failed = total_errors = 0
    any_failure = False

    for service_path in SERVICES:
        service_name = service_path.split("/")[-1]
        path, rc, output = run_service(service_path)

        # Parse summary line
        lines = output.strip().splitlines()
        summary = next((l for l in reversed(lines) if "passed" in l or "failed" in l or "error" in l), "no summary")

        status = "PASS" if rc == 0 else "FAIL"
        if rc != 0:
            any_failure = True
            print(f"\n{'─'*70}")
            print(f"  {service_name}: {status}")
            print(f"  {summary}")
            print(f"{'─'*70}")
            # Print only failures
            in_failure = False
            for line in lines:
                if line.startswith("FAILED") or line.startswith("ERROR") or "AssertionError" in line or "FAILURES" in line:
                    in_failure = True
                if in_failure:
                    print(f"  {line}")
        else:
            print(f"  {service_name}: {status}  —  {summary}")

        for line in lines:
            if "passed" in line:
                import re
                m = re.search(r"(\d+) passed", line)
                if m:
                    total_passed += int(m.group(1))
            if "failed" in line:
                import re
                m = re.search(r"(\d+) failed", line)
                if m:
                    total_failed += int(m.group(1))
            if "error" in line:
                import re
                m = re.search(r"(\d+) error", line)
                if m:
                    total_errors += int(m.group(1))

    print("\n" + "=" * 70)
    print(f"  TOTAL: {total_passed} passed  |  {total_failed} failed  |  {total_errors} errors")
    print("=" * 70)
    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
