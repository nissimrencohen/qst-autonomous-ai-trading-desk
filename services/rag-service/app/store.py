"""Document store backends.

`ChromaStore` is the production backend: persistent ChromaDB collection with
HuggingFace sentence-transformer embeddings. `InMemoryStore` is a
dependency-free keyword-overlap store for dev/CI. Both satisfy
`DocumentStore`, selected via `RAG_STORE_BACKEND`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


@dataclass(frozen=True)
class RetrievedDoc:
    id: str
    ticker: str
    source: str
    title: str
    text: str
    published_at: str
    distance: float | None = None


class DocumentStore(Protocol):
    name: str

    def add(self, docs: list[dict]) -> int: ...
    def query(self, ticker: str, question: str, k: int) -> list[RetrievedDoc]: ...
    def count(self) -> int: ...
    def ping(self) -> bool: ...


def _doc_id(doc: dict) -> str:
    if doc.get("id"):
        return doc["id"]
    digest = hashlib.sha1(
        f"{doc['ticker']}|{doc['title']}|{doc['text'][:200]}".encode()
    ).hexdigest()
    return f"{doc['ticker'].upper()}-{digest[:12]}"


class ChromaStore:
    """Persistent ChromaDB collection (cosine HNSW, HF embeddings)."""

    name = "chroma"

    def __init__(
        self,
        path: str | None = None,
        collection: str = "financial_docs",
        embedding_model: str | None = None,
    ) -> None:
        import chromadb
        from chromadb.utils.embedding_functions import (
            SentenceTransformerEmbeddingFunction,
        )

        self._client = chromadb.PersistentClient(path=path or settings.chroma_path)
        self._collection = self._client.get_or_create_collection(
            collection,
            embedding_function=SentenceTransformerEmbeddingFunction(
                model_name=embedding_model or settings.embedding_model
            ),
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, docs: list[dict]) -> int:
        ids = [_doc_id(d) for d in docs]
        self._collection.upsert(
            ids=ids,
            documents=[d["text"] for d in docs],
            metadatas=[
                {
                    "ticker": d["ticker"].upper(),
                    "source": d["source"],
                    "title": d["title"],
                    "published_at": d["published_at"],
                }
                for d in docs
            ],
        )
        return len(ids)

    def query(self, ticker: str, question: str, k: int) -> list[RetrievedDoc]:
        res = self._collection.query(
            query_texts=[question],
            n_results=k,
            where={"ticker": ticker.upper()},
        )
        out: list[RetrievedDoc] = []
        for i, doc_id in enumerate(res["ids"][0]):
            meta = res["metadatas"][0][i]
            out.append(
                RetrievedDoc(
                    id=doc_id,
                    ticker=meta["ticker"],
                    source=meta["source"],
                    title=meta["title"],
                    text=res["documents"][0][i],
                    published_at=meta["published_at"],
                    distance=round(res["distances"][0][i], 4)
                    if res.get("distances")
                    else None,
                )
            )
        return out

    def count(self) -> int:
        return self._collection.count()

    def ping(self) -> bool:
        try:
            self._client.heartbeat()
            return True
        except Exception:
            return False


class InMemoryStore:
    """Keyword-overlap retrieval — deterministic dev/CI fallback."""

    name = "memory"

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", text.lower()))

    def add(self, docs: list[dict]) -> int:
        for d in docs:
            doc = {**d, "id": _doc_id(d), "ticker": d["ticker"].upper()}
            self._docs[doc["id"]] = doc
        return len(docs)

    def query(self, ticker: str, question: str, k: int) -> list[RetrievedDoc]:
        q_tokens = self._tokens(question)
        scored = []
        for doc in self._docs.values():
            if doc["ticker"] != ticker.upper():
                continue
            overlap = len(q_tokens & self._tokens(doc["title"] + " " + doc["text"]))
            scored.append((overlap, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedDoc(
                id=doc["id"],
                ticker=doc["ticker"],
                source=doc["source"],
                title=doc["title"],
                text=doc["text"],
                published_at=doc["published_at"],
                distance=round(1.0 / (1 + overlap), 4),
            )
            for overlap, doc in scored[:k]
        ]

    def count(self) -> int:
        return len(self._docs)

    def ping(self) -> bool:
        return True


def build_store() -> DocumentStore:
    if settings.store_backend == "chroma":
        return ChromaStore()
    return InMemoryStore()
