"""Seed the RAG service with the mock financial corpus.

Usage:
    python scripts/seed_rag.py [--url http://localhost:8001] [--seed-file data/seed/financial_docs.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main(url: str, seed_file: Path) -> None:
    docs = json.loads(seed_file.read_text(encoding="utf-8"))["documents"]
    resp = httpx.post(f"{url.rstrip('/')}/ingest", json={"documents": docs}, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    print(
        f"ingested={body['ingested']} total={body['total_documents']} "
        f"store={body['store_backend']}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument(
        "--seed-file", type=Path, default=ROOT / "data" / "seed" / "financial_docs.json"
    )
    args = ap.parse_args()
    main(args.url, args.seed_file)
