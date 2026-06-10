"""API schemas for the RAG service."""
from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentIn(BaseModel):
    id: str | None = Field(default=None, description="Stable id; derived from content when omitted")
    ticker: str = Field(min_length=1, max_length=12, examples=["NVDA"])
    source: str = Field(examples=["Q1-2026 earnings call"])
    title: str
    text: str = Field(min_length=20)
    published_at: str = Field(examples=["2026-04-18"])


class IngestRequest(BaseModel):
    documents: list[DocumentIn] = Field(min_length=1, max_length=200)


class IngestResponse(BaseModel):
    ingested: int
    store_backend: str
    total_documents: int


class QueryRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12, examples=["ESLT"])
    question: str = Field(min_length=3, max_length=500)
    k: int = Field(default=4, ge=1, le=10)


class RetrievedDocOut(BaseModel):
    id: str
    ticker: str
    source: str
    title: str
    text: str
    published_at: str
    distance: float | None = None


class QueryResponse(BaseModel):
    ticker: str
    question: str
    retrieved: list[RetrievedDocOut]
    summary: str | None = Field(
        default=None, description="LLM summary of the retrieved context; null when nothing was retrieved"
    )
    store_backend: str
    summarizer_backend: str
