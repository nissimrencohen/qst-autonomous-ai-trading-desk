"""Streamlit admin panel — raw data submission for the trading desk.

Internal tool: ingest documents into the RAG corpus, quick-score chart
screenshots against the Vision Analyser, summarize pasted text with a local
Ollama model, and watch service health.
"""
from __future__ import annotations

import json
import os

import requests
import streamlit as st

RAG_URL = os.getenv("RAG_URL", "http://localhost:8001")
VISION_URL = os.getenv("VISION_URL", "http://localhost:8002")
AGENTIC_URL = os.getenv("AGENTIC_URL", "http://localhost:8003")
GUARDRAILS_URL = os.getenv("GUARDRAILS_URL", "http://localhost:8004")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

TICKERS = ["NVDA", "ESLT", "NXSN", "TOND", "CUE"]

# Family 5, Version 5 — final (pass rate 9/10, see docs/PROMPT_ENGINEERING_LOG.md)
OLLAMA_UI_SYSTEM_PROMPT = """\
You are the admin panel's local pre-ingest summarizer. You receive ONE raw
financial document pasted by an operator and produce a compact ingest
preview. You run fully offline; you know nothing beyond the pasted text.

Output exactly three lines, nothing else:
TITLE: <max 12 words, taken from the document's subject>
FACTS: <2-4 semicolon-separated facts copied from the text; keep every
number exactly as written; no facts that are not in the text>
FLAGS: <comma-separated subset of [no-numbers, opinion-heavy, stale-date,
off-topic, conflicting] or 'none'>

Never add commentary, advice, or facts from outside the document. If the
text is not a financial document, output FLAGS: off-topic and leave FACTS
empty.
"""

st.set_page_config(page_title="Trading Desk Admin", page_icon="🛠️", layout="wide")
st.title("🛠️ Trading Desk — Admin Panel")
st.caption("Raw data submission · internal tool. The trading dashboard lives on :3000.")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Service health")
    for name, base in [
        ("RAG", RAG_URL),
        ("Vision", VISION_URL),
        ("Agentic", AGENTIC_URL),
        ("Guardrails", GUARDRAILS_URL),
    ]:
        try:
            ready = requests.get(f"{base}/ready", timeout=3)
            ok = ready.status_code == 200
            st.write(("🟢" if ok else "🟠"), f"**{name}** — {base}")
            if not ok:
                st.json(ready.json(), expanded=False)
        except requests.RequestException:
            st.write("🔴", f"**{name}** — {base} (unreachable)")

ingest_tab, vision_tab, ollama_tab = st.tabs(
    ["📄 Ingest documents", "📈 Chart quick-score", "🦙 Local summarize (Ollama)"]
)

# ----------------------------------------------------------- ingest tab
with ingest_tab:
    st.subheader("Add a document to the RAG corpus")
    with st.form("single_doc"):
        col1, col2, col3 = st.columns(3)
        ticker = col1.selectbox("Ticker", TICKERS)
        source = col2.text_input("Source", placeholder="Q1-2026 earnings call")
        published_at = col3.date_input("Published").isoformat()
        title = st.text_input("Title")
        text = st.text_area("Document text", height=180)
        if st.form_submit_button("Ingest document", type="primary"):
            doc = {
                "ticker": ticker, "source": source, "title": title,
                "text": text, "published_at": published_at,
            }
            try:
                res = requests.post(f"{RAG_URL}/ingest", json={"documents": [doc]}, timeout=60)
                if res.ok:
                    body = res.json()
                    st.success(
                        f"Ingested. Corpus now holds {body['total_documents']} documents "
                        f"(store: {body['store_backend']})."
                    )
                else:
                    st.error(f"{res.status_code}: {res.text}")
            except requests.RequestException as exc:
                st.error(f"RAG service unreachable: {exc}")

    st.divider()
    st.subheader("Bulk ingest (JSON)")
    st.caption('Schema: {"documents": [{ticker, source, title, text, published_at}, …]}')
    uploaded = st.file_uploader("Seed file", type=["json"])
    if uploaded and st.button("Ingest file"):
        try:
            payload = json.load(uploaded)
            res = requests.post(
                f"{RAG_URL}/ingest", json={"documents": payload["documents"]}, timeout=120
            )
            st.success(res.json()) if res.ok else st.error(res.text)
        except (json.JSONDecodeError, KeyError) as exc:
            st.error(f"Bad seed file: {exc}")
        except requests.RequestException as exc:
            st.error(f"RAG service unreachable: {exc}")

# ----------------------------------------------------------- vision tab
with vision_tab:
    st.subheader("Quick-score a chart screenshot")
    col1, col2 = st.columns([1, 2])
    v_ticker = col1.selectbox("Ticker", TICKERS, key="vision_ticker")
    chart = col1.file_uploader("Chart image", type=["png", "jpg", "jpeg", "webp"])
    if chart:
        col2.image(chart, caption=chart.name, width="stretch")
    if chart and col1.button("Analyse", type="primary"):
        try:
            res = requests.post(
                f"{VISION_URL}/analyse",
                data={"ticker": v_ticker},
                files={"chart": (chart.name, chart.getvalue(), chart.type)},
                timeout=60,
            )
            if res.ok:
                body = res.json()
                label = body["label"]
                icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}[label]
                col2.metric(
                    f"{icon} {label.upper()} ({body['model_backend']})",
                    f"{body['score']:+.3f}",
                    f"confidence {body['confidence']:.2f}",
                )
                col2.json(body["patterns"], expanded=False)
            else:
                col2.error(f"{res.status_code}: {res.text}")
        except requests.RequestException as exc:
            col2.error(f"Vision service unreachable: {exc}")

# ----------------------------------------------------------- ollama tab
with ollama_tab:
    st.subheader("Pre-ingest summary via local Ollama")
    st.caption(
        f"Model: `{OLLAMA_MODEL}` at `{OLLAMA_URL}` — runs the Family-5 prompt "
        "(docs/PROMPT_ENGINEERING_LOG.md) to preview TITLE/FACTS/FLAGS before ingesting."
    )
    raw = st.text_area("Paste raw document text", height=220)
    if st.button("Summarize locally") and raw.strip():
        try:
            res = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "system": OLLAMA_UI_SYSTEM_PROMPT,
                    "prompt": raw,
                    "stream": False,
                },
                timeout=180,
            )
            if res.ok:
                st.code(res.json()["response"].strip())
            else:
                st.error(f"{res.status_code}: {res.text}")
        except requests.RequestException as exc:
            st.error(f"Ollama unreachable at {OLLAMA_URL}: {exc}")
