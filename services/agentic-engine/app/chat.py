"""Conversational Assistant API — chat endpoint for the React sidebar.

Routes
──────
  POST /chat/                  — send a message, get a streaming SSE response
  POST /chat/sync              — send a message, get full JSON (non-streaming)
  GET  /chat/sessions          — list sessions (last 20)
  GET  /chat/history/{sid}     — load message history for a session

Guardrails Integration (V2.0 security fix)
───────────────────────────────────────────
  EVERY user message is validated by POST {guardrails_url}/validate/input
  BEFORE any LLM call.  Failure policy: fail-open (guardrails outage must
  not take the chat down), same pattern as orchestrator._validate_input().

  Response contract:
    {allowed: bool, violations: [{rule, detail, excerpt}], backend: str}

  If allowed=False → StreamingResponse emits a single SSE "blocked" event
  containing the violation reasons, then closes.  The LLM is never called.

Cloud-First Routing
───────────────────
  Calls litellm.completion() directly (no CrewAI overhead) using the same
  provider_chain() as the rest of the system: Groq → Gemini → GitHub → OpenAI.
  Helicone proxy + cost tagging is applied automatically via provider_chain().

Domain Grounding
────────────────
  A strict system prompt confines the assistant to quantitative trading topics.
  The guardrails input rail provides a second, rule-based enforcement layer —
  the system prompt is NOT a substitute, it is an additional layer.

Dual-Persistence
────────────────
  Every turn is persisted to SQLite via ChatStore.
  InsightExtractor runs async (best-effort) after the response is sent.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

import requests

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)
chat_router = APIRouter(prefix="/chat", tags=["assistant"])

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are the Autonomous AI Trading Desk's quantitative analyst assistant — \
codenamed QST INTELLIGENCE.

Your domain is STRICTLY limited to:
  • Quantitative and algorithmic trading strategies
  • Technical analysis (chart patterns, indicators: RSI, MACD, ATR, MAs, etc.)
  • Fundamental analysis (earnings, revenue, margins, catalysts)
  • Portfolio risk management (position sizing, drawdown, Sharpe, correlation)
  • Market microstructure and options theory (VIX, term structure, IV, Greeks)
  • Financial market data interpretation
  • The system's own reports, signals, and agent outputs
  • The 10 approved instruments: SPCX, MSFT, AAPL, NVDA, GOOGL, AMZN, UPRO, \
TQQQ, VIXY, SVXY

For ANY request outside this domain, respond politely but firmly:
  "I'm the QST trading analyst assistant. My expertise is confined to \
quantitative finance and the trading instruments on our watchlist. I'm not \
able to help with [topic], but happy to analyse markets or interpret our \
system's reports."

Tone: precise, professional, data-driven. Use bullet points for multi-part \
answers. Cite ticker symbols in uppercase. Never provide investment advice \
(add a brief disclaimer on execution recommendations). You may reference \
market regimes (calm/elevated/stress/panic), VIX term structure \
(contango/backwardation), and risk levels (low/medium/high) as used by this \
system.

Current system context: Multi-agent crew (Technical Analyst + Fundamental \
Analyst + Risk Manager), LLM routing: Groq → Gemini → GitHub → OpenAI, \
vector store: ChromaDB, persistence: SQLite + AWS Bedrock KB (future).
"""

# ── Pydantic models ───────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant|system)$")
    content: str = Field(..., min_length=0, max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=50)
    session_id: str | None = None
    ticker_context: str | None = None   # e.g. "NVDA" — added to metadata


class ChatSyncResponse(BaseModel):
    session_id: str
    reply: str
    model_used: str
    insight_embedded: bool
    guardrails_checked: bool


# ── Guardrails pre-flight ─────────────────────────────────────────────────────


def _check_guardrails_input(
    question: str, ticker: str | None
) -> tuple[bool, list[str]]:
    """Call POST {guardrails_url}/validate/input before any LLM call.

    Returns (allowed, reasons).  Fails OPEN on any network/service error so a
    guardrails outage never takes the chat down — identical policy to
    orchestrator._validate_input().

    The guardrails service is the SECOND enforcement layer; the system prompt
    is the FIRST.  Both must pass for a response to be generated.
    """
    try:
        r = requests.post(
            f"{settings.guardrails_url}/validate/input",
            json={
                "question": question,
                "ticker": ticker,
                "source": "chat-assistant",
            },
            timeout=8,  # fast timeout — chat is interactive
        )
        if r.status_code != 200:
            log.warning(
                "guardrails /validate/input returned %s for chat; degrading open",
                r.status_code,
            )
            return True, []
        body = r.json()
        reasons = [v.get("detail", v.get("rule", "blocked")) for v in body.get("violations", [])]
        allowed = bool(body.get("allowed", True))
        if not allowed:
            log.warning(
                "guardrails BLOCKED chat message: violations=%s",
                [v.get("rule") for v in body.get("violations", [])],
            )
        return allowed, reasons
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "guardrails unreachable for chat (%s); degrading open", exc
        )
        return True, []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_messages(history: list[ChatMessage]) -> list[dict]:
    """Prepend the system prompt to the message list."""
    msgs: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    # Keep last 20 turns max to stay within context windows
    for m in history[-20:]:
        msgs.append({"role": m.role, "content": m.content})
    return msgs


def _first_provider() -> tuple[str, dict] | None:
    """Return (model, kwargs) for the first available provider."""
    try:
        from app.llm_router import provider_chain
        chain = provider_chain()
        return chain[0] if chain else None
    except Exception as exc:  # noqa: BLE001
        log.warning("chat: could not build provider chain — %s", exc)
        return None


def _sse(event_type: str, data: dict) -> str:
    """Format a single SSE data frame."""
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"


# ── Streaming SSE generator ───────────────────────────────────────────────────


async def _stream_chat(
    messages: list[dict],
    model: str,
    litellm_kwargs: dict,
) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted chunks from litellm streaming completion."""
    import litellm

    safe_kwargs = {k: v for k, v in litellm_kwargs.items() if k != "extra_headers"}
    extra_headers = litellm_kwargs.get("extra_headers", {})

    accumulated = ""
    model_used = model

    try:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=True,
            temperature=0.3,
            max_tokens=1500,
            extra_headers=extra_headers if extra_headers else None,
            **safe_kwargs,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            if content:
                accumulated += content
                yield _sse("delta", {"content": content})

    except Exception as exc:  # noqa: BLE001
        log.error("chat stream error (model=%s): %s", model, exc)
        # Try remaining providers in the chain
        try:
            from app.llm_router import provider_chain
            chain = provider_chain()
            for fb_model, fb_kwargs in chain:
                if fb_model == model:
                    continue
                try:
                    fb_safe = {k: v for k, v in fb_kwargs.items() if k != "extra_headers"}
                    fb_headers = fb_kwargs.get("extra_headers", {})
                    response = await litellm.acompletion(
                        model=fb_model,
                        messages=messages,
                        stream=True,
                        temperature=0.3,
                        max_tokens=1500,
                        extra_headers=fb_headers if fb_headers else None,
                        **fb_safe,
                    )
                    model_used = fb_model
                    async for chunk in response:
                        delta = chunk.choices[0].delta
                        content = getattr(delta, "content", None) or ""
                        if content:
                            accumulated += content
                            yield _sse("delta", {"content": content})
                    break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            yield _sse("error", {"content": "All providers unavailable. Please retry."})

    # Final done event carries metadata
    yield _sse("done", {"model_used": model_used, "accumulated": accumulated})


# ── Routes ────────────────────────────────────────────────────────────────────


@chat_router.post("/")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Send a message; receive a streaming SSE response.

    Pre-flight guardrails check → LLM streaming → dual-persistence.
    Guardrails rejection emits a single SSE 'blocked' event and closes.
    """
    store = getattr(request.app.state, "chat_store", None)
    extractor = getattr(request.app.state, "insight_extractor", None)

    user_msg_content = payload.messages[-1].content

    # Ensure session exists in DB
    session_id = payload.session_id
    if store:
        words = user_msg_content.split()
        title = " ".join(words[:5]) + ("..." if len(words) > 5 else "")
        session_id = store.ensure_session(session_id, title=title)

    # ── GUARDRAILS PRE-FLIGHT ─────────────────────────────────────────────────
    allowed, reasons = await asyncio.to_thread(
        _check_guardrails_input, user_msg_content, payload.ticker_context
    )

    if not allowed:
        # Persist the blocked user message so audit log is complete
        if store:
            store.append_message(session_id, "user", user_msg_content)
            refusal = (
                "⚠ This request was flagged by the QST guardrails system "
                "and cannot be processed. "
                + ("; ".join(reasons) if reasons else "Request outside desk policy.")
            )
            store.append_message(session_id, "assistant", refusal)

        async def _blocked_stream():
            msg = (
                "⚠ This request was flagged by the QST guardrails system "
                "and cannot be processed. "
                + ("; ".join(reasons) if reasons else "Request outside desk policy.")
            )
            yield _sse("delta", {"content": msg})
            yield _sse("done", {"model_used": "guardrails", "accumulated": msg})
            yield _sse("session_id", {"session_id": session_id})

        return StreamingResponse(
            _blocked_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            },
        )
    # ── END GUARDRAILS ────────────────────────────────────────────────────────

    # Persist the user message only after guardrails passes
    if store:
        store.append_message(session_id, "user", user_msg_content)

    entry = _first_provider()
    if entry is None:
        raise HTTPException(503, "No LLM providers available")

    model, litellm_kwargs = entry
    messages = _build_messages(payload.messages)

    async def generate():
        full_reply = ""
        model_used = model

        async for chunk in _stream_chat(messages, model, litellm_kwargs):
            yield chunk
            if chunk.startswith("data: "):
                try:
                    data = json.loads(chunk[6:])
                    if data.get("type") == "done":
                        full_reply = data.get("accumulated", "")
                        model_used = data.get("model_used", model)
                except (json.JSONDecodeError, KeyError):
                    pass

        # Persist assistant reply + run insight extractor (best-effort)
        if store and full_reply:
            store.append_message(session_id, "assistant", full_reply, model_used=model_used)
            if extractor:
                try:
                    extractor.maybe_embed(
                        session_id=session_id,
                        user_msg=user_msg_content,
                        ai_msg=full_reply,
                        ticker=payload.ticker_context,
                        model_used=model_used,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("InsightExtractor error: %s", exc)

        # Surface session_id so frontend can store it
        yield _sse("session_id", {"session_id": session_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@chat_router.post("/sync", response_model=ChatSyncResponse)
async def chat_sync(payload: ChatRequest, request: Request) -> ChatSyncResponse:
    """Non-streaming fallback — returns full JSON response.

    Same guardrails pre-flight as the streaming endpoint.
    """
    import litellm

    store = getattr(request.app.state, "chat_store", None)
    extractor = getattr(request.app.state, "insight_extractor", None)

    user_msg_content = payload.messages[-1].content

    session_id = payload.session_id
    if store:
        words = user_msg_content.split()
        title = " ".join(words[:5]) + ("..." if len(words) > 5 else "")
        session_id = store.ensure_session(session_id, title=title)

    # ── GUARDRAILS PRE-FLIGHT ─────────────────────────────────────────────────
    allowed, reasons = await asyncio.to_thread(
        _check_guardrails_input, user_msg_content, payload.ticker_context
    )

    if not allowed:
        if store:
            store.append_message(session_id, "user", user_msg_content)
            refusal = (
                "⚠ Request blocked by guardrails: "
                + ("; ".join(reasons) if reasons else "outside desk policy.")
            )
            store.append_message(session_id, "assistant", refusal)
        raise HTTPException(
            status_code=422,
            detail={
                "blocked": True,
                "reasons": reasons,
                "message": "Request blocked by QST guardrails.",
            },
        )
    # ── END GUARDRAILS ────────────────────────────────────────────────────────

    if store:
        store.append_message(session_id, "user", user_msg_content)

    messages = _build_messages(payload.messages)

    from app.llm_router import provider_chain
    chain = provider_chain()
    if not chain:
        raise HTTPException(503, "No LLM providers available")

    reply = ""
    model_used = ""
    for try_model, try_kwargs in chain:
        try:
            safe = {k: v for k, v in try_kwargs.items() if k != "extra_headers"}
            headers = try_kwargs.get("extra_headers", {})
            resp = await asyncio.to_thread(
                litellm.completion,
                model=try_model,
                messages=messages,
                temperature=0.3,
                max_tokens=1500,
                extra_headers=headers if headers else None,
                **safe,
            )
            reply = resp.choices[0].message.content or ""
            model_used = try_model
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("chat/sync: %s failed — %s", try_model, exc)

    if not reply:
        raise HTTPException(503, "All LLM providers failed")

    insight_embedded = False
    if store:
        store.append_message(session_id, "assistant", reply, model_used=model_used)
    if extractor:
        try:
            insight_embedded = extractor.maybe_embed(
                session_id=session_id,
                user_msg=user_msg_content,
                ai_msg=reply,
                ticker=payload.ticker_context,
                model_used=model_used,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("InsightExtractor error: %s", exc)

    return ChatSyncResponse(
        session_id=session_id,
        reply=reply,
        model_used=model_used,
        insight_embedded=insight_embedded,
        guardrails_checked=True,
    )


@chat_router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    """Return the last 20 chat sessions for the session picker."""
    store = getattr(request.app.state, "chat_store", None)
    if store is None:
        return {"sessions": []}
    return {"sessions": store.list_sessions()}


@chat_router.get("/history/{session_id}")
async def get_history(session_id: str, request: Request) -> dict:
    """Return message history for a given session (for UI restore on reload)."""
    store = getattr(request.app.state, "chat_store", None)
    if store is None:
        return {"session_id": session_id, "messages": []}
    messages = store.get_history(session_id)
    return {"session_id": session_id, "messages": messages}
