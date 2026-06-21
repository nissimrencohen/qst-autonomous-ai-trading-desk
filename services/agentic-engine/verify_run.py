import sqlite3
import requests
import time
import json

def check_dbs():
    print("=== Database Status ===")
    try:
        conn = sqlite3.connect("./data/ingestion.db")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ingested_data")
        count = cur.fetchone()[0]
        cur.execute("SELECT ticker, count(*) FROM ingested_data GROUP BY ticker")
        by_ticker = cur.fetchall()
        print(f"IngestionStore Total Rows: {count}")
        for t, c in by_ticker:
            print(f"  {t}: {c}")
        conn.close()
    except Exception as e:
        print(f"Ingestion DB Error: {e}")

    try:
        conn = sqlite3.connect("./data/synthesis_reports.db")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM synthesis_reports")
        count = cur.fetchone()[0]
        cur.execute("SELECT ticker, updated_at FROM synthesis_reports")
        reports = cur.fetchall()
        print(f"\nReportStore Total Reports: {count}")
        for t, updated in reports:
            print(f"  {t} (updated: {updated})")
        conn.close()
    except Exception as e:
        print(f"Report DB Error: {e}")

def check_apis():
    print("\n=== API Status ===")
    endpoints = [
        "/health",
        "/ingestion/status",
        "/synthesis/status"
    ]
    for ep in endpoints:
        start = time.time()
        try:
            res = requests.get(f"http://127.0.0.1:8003{ep}", timeout=5)
            elapsed = time.time() - start
            print(f"GET {ep} -> {res.status_code} (took {elapsed:.3f}s)")
            if "status" in res.json() or "ready" in res.json():
                print(f"  {json.dumps(res.json())[:100]}...")
        except Exception as e:
            print(f"GET {ep} -> Failed: {e}")

if __name__ == "__main__":
    check_dbs()
    check_apis()
