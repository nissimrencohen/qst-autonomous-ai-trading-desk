"""Decode n8n's flattened execution-data array and print error fields."""
from __future__ import annotations

import json
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
row = con.execute(
    "SELECT id FROM execution_entity WHERE workflowId='TradingDeskWf001' "
    "ORDER BY id DESC LIMIT 1"
).fetchone()
data = con.execute(
    "SELECT data FROM execution_data WHERE executionId=?", (row[0],)
).fetchone()[0]
arr = json.loads(data)


def deref(value, depth=0):
    if depth > 6:
        return value
    if isinstance(value, str) and value.isdigit():
        return deref(arr[int(value)], depth + 1)
    if isinstance(value, dict):
        return {k: deref(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [deref(v, depth + 1) for v in value]
    return value


root = deref(arr[0])
result = root.get("resultData", {})
err = result.get("error", {})
print("lastNodeExecuted:", result.get("lastNodeExecuted"))
print("error message:", err.get("message"))
print("description:", err.get("description"))
cause = err.get("cause") or {}
if isinstance(cause, dict):
    print("cause message:", cause.get("message"), "| code:", cause.get("code"), "| status:", cause.get("status"))
