"""Summarization backends behind one provider abstraction.

`BedrockSummarizer` (primary), `LiteLLMSummarizer` (multi-provider router),
and `OllamaSummarizer` (local tasks) call real LLMs; `ExtractiveSummarizer`
is a deterministic lexical fallback for dev/CI and degraded mode.
Selected via `RAG_SUMMARIZER_BACKEND`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

log = logging.getLogger(__name__)

from app.config import settings
from app.prompts import RAG_SUMMARY_SYSTEM_PROMPT, build_user_prompt
from app.store import RetrievedDoc


class Summarizer(Protocol):
    name: str

    def summarize(self, ticker: str, question: str, docs: list[RetrievedDoc]) -> str: ...


class BedrockSummarizer:
    """AWS Bedrock (Anthropic messages API)."""

    name = "bedrock"

    def __init__(self, model_id: str | None = None, region: str | None = None) -> None:
        import boto3

        self._model_id = model_id or settings.bedrock_model_id
        self._client = boto3.client(
            "bedrock-runtime", region_name=region or settings.aws_region
        )

    def summarize(self, ticker: str, question: str, docs: list[RetrievedDoc]) -> str:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 700,
            "system": RAG_SUMMARY_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": build_user_prompt(ticker, question, docs)}
            ],
        }
        resp = self._client.invoke_model(
            modelId=self._model_id, body=json.dumps(body)
        )
        payload = json.loads(resp["body"].read())
        return payload["content"][0]["text"].strip()


class OllamaSummarizer:
    """Local Ollama / llama.cpp-served model."""

    name = "ollama"
    # reasoning models (qwen3, deepseek-r1) prepend <think>...</think>
    _REASONING_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self._base_url = (base_url or settings.ollama_url).rstrip("/")
        self._model = model or settings.ollama_model

    def summarize(self, ticker: str, question: str, docs: list[RetrievedDoc]) -> str:
        import httpx

        payload = {
            "model": self._model,
            "system": RAG_SUMMARY_SYSTEM_PROMPT,
            "prompt": build_user_prompt(ticker, question, docs),
            "stream": False,
            # reasoning models burn minutes thinking; the summary task doesn't
            # need it (measured: 223s -> 23s on qwen3:8b)
            "think": False,
        }
        resp = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=300)
        if resp.status_code == 400:  # older Ollama without the think parameter
            payload.pop("think")
            resp = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=300)
        resp.raise_for_status()
        text = resp.json()["response"]
        return self._REASONING_BLOCK.sub("", text).strip()


class LiteLLMSummarizer:
    """Multi-provider summarizer: Groq → Gemini → OpenAI → Ollama with fallback."""

    name = "litellm"

    def summarize(self, ticker: str, question: str, docs: list[RetrievedDoc]) -> str:
        import litellm
        from app.llm_router import provider_chain

        msgs = [
            {"role": "system", "content": RAG_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(ticker, question, docs)},
        ]
        chain = provider_chain()
        if not chain:
            raise RuntimeError(
                "No LLM providers available — set RAG_GROQ_API_KEY / "
                "RAG_GOOGLE_API_KEY / RAG_OPENAI_API_KEY or ensure Ollama is up."
            )
        last_exc: Exception | None = None
        for model, kwargs in chain:
            try:
                resp = litellm.completion(
                    model=model,
                    messages=msgs,
                    max_tokens=700,
                    temperature=0.2,
                    **kwargs,
                )
                return resp.choices[0].message.content.strip()
            except Exception as exc:
                log.warning("LiteLLM provider %s failed: %s", model, exc)
                last_exc = exc
        raise RuntimeError(f"All LLM providers exhausted. Last error: {last_exc}")


class ExtractiveSummarizer:
    """Deterministic lexical summary: top sentences by question-term overlap."""

    name = "extractive"
    _MAX_SENTENCES = 5

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", text.lower()))

    def summarize(self, ticker: str, question: str, docs: list[RetrievedDoc]) -> str:
        q_tokens = self._tokens(question) | {ticker.lower()}
        scored: list[tuple[int, str, str]] = []
        for doc in docs:
            for sentence in re.split(r"(?<=[.!?])\s+", doc.text):
                if len(sentence) < 25:
                    continue
                overlap = len(q_tokens & self._tokens(sentence))
                scored.append((overlap, sentence.strip(), doc.title))
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[: self._MAX_SENTENCES]
        if not top or top[0][0] == 0:
            return "The retrieved context does not cover this."
        bullets = "\n".join(f"- {s} [source: {title}]" for _, s, title in top)
        coverage = ", ".join(dict.fromkeys(title for _, _, title in top))
        return f"{bullets}\nCoverage: {coverage}"


def build_summarizer() -> Summarizer:
    backend = settings.summarizer_backend
    if backend == "bedrock":
        return BedrockSummarizer()
    if backend == "litellm":
        return LiteLLMSummarizer()
    if backend == "ollama":
        return OllamaSummarizer()
    return ExtractiveSummarizer()
