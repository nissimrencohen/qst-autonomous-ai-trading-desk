"""RAG service API routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.schemas import (
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    RetrievedDocOut,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ingest", response_model=IngestResponse, tags=["rag"])
def ingest(payload: IngestRequest, request: Request) -> IngestResponse:
    """Add documents (financial reports, news) to the vector store."""
    store = request.app.state.store
    ingested = store.add([d.model_dump() for d in payload.documents])
    log.info("ingested %d documents (total=%d)", ingested, store.count())
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
