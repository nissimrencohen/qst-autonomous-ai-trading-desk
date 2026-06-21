"""RAG service API routes."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request, File, Form, UploadFile, HTTPException
import httpx
import io
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from app.schemas import (
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    RetrievedDocOut,
)

log = logging.getLogger(__name__)
router = APIRouter()

# /market-live makes ~25 synchronous yfinance calls; cache the computed snapshot
# for _MARKET_TTL seconds and compute it in a worker thread so the event loop is
# never blocked. The dashboard auto-refreshes every 90 s, so a 90 s TTL means at
# most one yfinance storm per refresh cycle regardless of how many tabs poll.
_MARKET_TTL = 90.0
_market_cache: dict = {"data": None, "ts": 0.0}
_market_lock = asyncio.Lock()


@router.post("/ingest", response_model=IngestResponse, tags=["rag"])
def ingest(payload: IngestRequest, request: Request) -> IngestResponse:
    """Add documents (financial reports, news) to the vector store."""
    store = request.app.state.store
    ingested = store.add([d.model_dump() for d in payload.documents])
    log.info("ingested %d documents (total=%d)", ingested, store.count())
    return IngestResponse(
        ingested=ingested, store_backend=store.name, total_documents=store.count()
    )


@router.post("/ingest-omnibus", response_model=IngestResponse, tags=["rag"])
async def ingest_omnibus(
    request: Request,
    ticker: str = Form(...),
    source: str = Form(...),
    published_at: str = Form(...),
    files: list[UploadFile] = File(None),
    text: str = Form(""),
) -> IngestResponse:
    """Omnibus ingest: handles text, PDF, CSV, MD natively; routes images to vision-analyser."""
    store = request.app.state.store
    docs_to_add = []
    
    if text and text.strip():
        docs_to_add.append({
            "ticker": ticker,
            "source": source,
            "title": "Manual Text Snippet",
            "text": text.strip(),
            "published_at": published_at
        })
        
    for f in (files or []):
        content = await f.read()
        if not content:
            continue
            
        doc_text = ""
        filename = f.filename or "unknown"
        
        if filename.lower().endswith(".pdf"):
            if PdfReader is None:
                doc_text = "[pypdf not installed, cannot parse PDF]"
            else:
                try:
                    reader = PdfReader(io.BytesIO(content))
                    doc_text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
                except Exception as e:
                    log.warning("failed to parse PDF %s: %s", filename, e)
                    doc_text = f"[PDF Parsing Failed: {e}]"
                
        elif filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            try:
                vision_url = "http://vision-analyser:8002/describe"
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        vision_url,
                        files={"image": (filename, content, f.content_type or "image/png")},
                        timeout=30.0
                    )
                    if resp.status_code == 200:
                        doc_text = resp.json().get("description", "")
                    else:
                        doc_text = f"[Vision Analyser Error: {resp.status_code} {resp.text}]"
            except Exception as e:
                log.warning("failed to call vision-analyser for %s: %s", filename, e)
                doc_text = f"[Vision Analyser Exception: {e}]"
        else:
            # Assume text based
            try:
                doc_text = content.decode("utf-8")
            except UnicodeDecodeError:
                doc_text = content.decode("latin-1", errors="replace")
                
        if doc_text.strip():
            docs_to_add.append({
                "ticker": ticker,
                "source": f"{source} ({filename})",
                "title": filename,
                "text": doc_text.strip(),
                "published_at": published_at
            })
            
    if not docs_to_add:
        raise HTTPException(status_code=400, detail="No valid content provided for ingestion")
        
    ingested = store.add(docs_to_add)
    log.info("omnibus ingested %d documents (total=%d)", ingested, store.count())
    return IngestResponse(
        ingested=ingested, store_backend=store.name, total_documents=store.count()
    )



@router.post("/query", response_model=QueryResponse, tags=["rag"])
def query(payload: QueryRequest, request: Request) -> QueryResponse:
    """Retrieve top-k context for a ticker and summarize it for the agents."""
    store = request.app.state.store
    summarizer = request.app.state.summarizer

    docs = store.query(payload.ticker, payload.question, payload.k)
    summary = (
        summarizer.summarize(payload.ticker, payload.question, docs) if docs else None
    )
    log.info(
        "query ticker=%s k=%d retrieved=%d summarizer=%s",
        payload.ticker, payload.k, len(docs), summarizer.name,
    )
    return QueryResponse(
        ticker=payload.ticker.upper(),
        question=payload.question,
        retrieved=[RetrievedDocOut(**d.__dict__) for d in docs],
        summary=summary,
        store_backend=store.name,
        summarizer_backend=summarizer.name,
    )


@router.get("/market-live", tags=["market"])
async def market_live() -> dict:
    """Live market command center — prices, signals, entry/exit, position sizing.

    Served from a short-lived cache (computed in a worker thread) so the ~25
    yfinance calls never block the event loop or stall the dashboard.
    """
    now = time.monotonic()
    if _market_cache["data"] is not None and now - _market_cache["ts"] < _MARKET_TTL:
        return _market_cache["data"]
    async with _market_lock:
        # Double-check: another request may have refreshed while we waited.
        now = time.monotonic()
        if _market_cache["data"] is not None and now - _market_cache["ts"] < _MARKET_TTL:
            return _market_cache["data"]
        data = await asyncio.to_thread(_compute_market_live)
        _market_cache["data"] = data
        _market_cache["ts"] = time.monotonic()
        return data


async def refresh_market_cache() -> None:
    """Warm/refresh the snapshot off the request path (called at startup)."""
    try:
        data = await asyncio.to_thread(_compute_market_live)
        _market_cache["data"] = data
        _market_cache["ts"] = time.monotonic()
        log.info("market-live cache warmed (%d tickers)", len(data.get("tickers", {})))
    except Exception as exc:
        log.warning("market-live warm failed: %s", exc)


def _compute_market_live() -> dict:
    """Heavy synchronous snapshot builder (yfinance). Run via a worker thread."""
    import yfinance as yf

    def _price(sym: str) -> float | None:
        try:
            fi = yf.Ticker(sym).fast_info
            p = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            return float(p) if p else None
        except Exception:
            return None

    def _prev(sym: str) -> float | None:
        try:
            return float(yf.Ticker(sym).fast_info.previous_close or 0) or None
        except Exception:
            return None

    def _hist(sym: str) -> list[float]:
        try:
            df = yf.Ticker(sym).history(period="3mo", interval="1d")
            return df["Close"].dropna().tolist() if not df.empty else []
        except Exception:
            return []

    def _signal(price: float, hist: list[float]) -> dict:
        if len(hist) < 20:
            return {
                "signal": "neutral", "strength": 0.5,
                "ma20": price, "ma50": price, "atr": price * 0.02,
                "entry_zone": [round(price * 0.97, 2), round(price * 0.995, 2)],
                "target": round(price * 1.07, 2),
                "stop": round(price * 0.93, 2),
                "risk_reward": 1.0,
            }
        ma20 = sum(hist[-20:]) / 20
        ma50 = sum(hist[-50:]) / 50 if len(hist) >= 50 else ma20
        atr = sum(abs(hist[i] - hist[i - 1]) for i in range(-13, 0)) / 14 if len(hist) >= 15 else price * 0.02

        if price > ma20 * 1.005 and ma20 > ma50 * 0.999:
            sig, str_ = "bullish", min(0.95, 0.55 + (price / ma20 - 1) * 8)
            entry_lo = max(ma20 * 0.97, price - atr * 1.2)
            entry_hi = ma20 * 1.005
            target = price + atr * 4
            stop = price - atr * 2.5
        elif price < ma20 * 0.995 and ma20 < ma50 * 1.001:
            sig, str_ = "bearish", min(0.95, 0.55 + (1 - price / ma20) * 8)
            entry_lo = ma20 * 0.995
            entry_hi = min(ma20 * 1.03, price + atr * 1.2)
            target = price - atr * 4
            stop = price + atr * 2.5
        else:
            sig, str_ = "neutral", 0.48
            entry_lo, entry_hi = price * 0.97, price * 1.0
            target = price * 1.06
            stop = price * 0.94

        rr = abs(target - price) / max(abs(price - stop), 0.01)
        return {
            "signal": sig, "strength": round(str_, 2),
            "ma20": round(ma20, 2), "ma50": round(ma50, 2), "atr": round(atr, 2),
            "entry_zone": [round(entry_lo, 2), round(entry_hi, 2)],
            "target": round(target, 2), "stop": round(stop, 2),
            "risk_reward": round(rr, 2),
        }

    def _sizing(price: float, stop: float) -> dict:
        risk_per = abs(price - stop)
        if risk_per < 0.01:
            risk_per = price * 0.05
        out = {}
        for size in [10_000, 50_000, 100_000, 500_000, 1_000_000]:
            shares = max(1, int(size * 0.02 / risk_per))
            notional = round(shares * price)
            out[str(size)] = {
                "shares": shares,
                "notional": notional,
                "pct": round(notional / size * 100, 1),
            }
        return out

    # VIX Regime
    vix_p = _price("^VIX") or 20.0
    vix3m = _price("^VIX3M") or vix_p * 1.08
    vix9d = _price("^VIX9D") or vix_p * 0.96
    spread = vix3m - vix_p
    term_structure = "flat" if abs(spread) < 0.25 else ("contango" if spread > 0 else "backwardation")
    regime = "calm" if vix_p < 15 else "elevated" if vix_p < 20 else "stress" if vix_p < 30 else "panic"
    regime_advice = {
        "calm": "Deploy 90% exposure. VIX cheap — hedge cost minimal. Favor short-vol (SVXY); fade VIXY spikes.",
        "elevated": "Reduce to 75% exposure. Buy 2-3% VIX calls as tail hedge.",
        "stress": "Defensive 55% exposure. Raise cash. Short high-beta. Active stops.",
        "panic": "Max defensive 35%. Full hedges. Wait for VIX mean-reversion above 35.",
    }[regime]
    # Long-vol (VIXY) signal from term structure — contango bleeds long-vol via roll decay.
    uvxy_signal = "AVOID LONG VIXY" if term_structure == "contango" else (
        "SHORT-VOL EDGE (SVXY)" if regime == "calm" else "WATCH"
    )

    # Market Indices
    indices: dict = {}
    for sym in ["SPY", "QQQ", "^GSPC", "^NDX"]:
        p = _price(sym)
        pc = _prev(sym)
        if p and pc and pc > 0:
            indices[sym] = {
                "price": round(p, 2),
                "change_pct": round((p - pc) / pc * 100, 2),
            }

    # Main Tickers — the V2.0 10-symbol watchlist (legacy demo tickers removed).
    focus = {
        "SPCX": "Procure Space ETF · SpaceX",
        "MSFT": "Microsoft · Cloud/AI",
        "AAPL": "Apple · Consumer Tech",
        "NVDA": "NVIDIA Corp · AI/GPU",
        "GOOGL": "Alphabet Inc · AI/Cloud",
        "AMZN": "Amazon · Cloud/Retail",
        "UPRO": "UltraPro S&P 500 · 3x",
        "TQQQ": "UltraPro QQQ · 3x",
        "VIXY": "VIX Short-Term Futures · long-vol",
        "SVXY": "Short VIX · inverse",
    }
    tickers_out: dict = {}
    for sym, name in focus.items():
        try:
            p = _price(sym)
            pc = _prev(sym)
            if not p:
                continue
            chg = round((p - pc) / pc * 100, 2) if pc and pc > 0 else 0.0
            h = _hist(sym)
            sig = _signal(p, h)
            tickers_out[sym] = {
                "name": name,
                "price": round(p, 2),
                "change_pct": chg,
                **sig,
                "position_sizes": _sizing(p, sig["stop"]),
                "52w_high": round(max(h), 2) if h else None,
                "52w_low": round(min(h), 2) if h else None,
            }
        except Exception as e:
            log.warning("market_live ticker=%s err=%s", sym, e)

    heat_map = {
        "calm": ("low", 90),
        "elevated": ("medium", 72),
        "stress": ("high", 52),
        "panic": ("extreme", 30),
    }
    heat, exposure = heat_map[regime]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vix": {
            "price": round(vix_p, 2),
            "vix_9d": round(vix9d, 2),
            "vix_30d": round(vix_p, 2),
            "vix_3m": round(vix3m, 2),
            "term_structure": term_structure,
            "regime": regime,
            "spread": round(spread, 2),
            "uvxy_signal": uvxy_signal,
            "regime_advice": regime_advice,
        },
        "indices": indices,
        "tickers": tickers_out,
        "risk_summary": {
            "market_heat": heat,
            "recommended_exposure_pct": exposure,
            "hedging_advice": regime_advice,
        },
    }
