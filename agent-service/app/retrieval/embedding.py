"""Dense embedding providers, and the honest answer when there is none.

`HashEmbeddingProvider` used to be the silent stand-in whenever no embedding key
was configured. That is worse than having no dense channel at all: the vectors are
a hashed bag of tokens, so their cosine similarity carries no semantic signal, yet
the service still advertised hybrid retrieval and fused that noise into every
result — invisibly.

The contract is now explicit:

* `default_embedding_provider()` returns `None` when nothing real is configured, so
  the pipeline runs **lexical-only** and reports that mode instead of fusing noise;
* the hash provider stays available for tests and offline development, but only
  behind the `ALLOW_HASH_EMBEDDINGS` opt-in (default off), and it logs a warning
  when it is constructed so it can never be reached by accident;
* the dense channel is additive — `hybrid_search` always runs the lexical channels
  and only *adds* the dense one, so a missing or failing provider degrades recall
  rather than emptying the result.

**Batching and the event loop.** Embedding used to be one synchronous HTTP request
per chunk, issued from inside an `async` graph node. That is two defects at once:
an N+1 call pattern (N chunks → N round trips per query) and a blocking call on the
event loop, which stalls every other in-flight request. Providers therefore expose
`embed_batch` (many texts, one request) and `aembed_batch` (the same, off the event
loop). Persisting the vectors at ingest is what removes the per-query work
entirely — see `app/retrieval/vector_store.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from typing import Any, Protocol, Sequence, runtime_checkable

import httpx

from app.config import settings
from app.retrieval.types import RETRIEVAL_MODE_HYBRID, RETRIEVAL_MODE_LEXICAL_ONLY

logger = logging.getLogger(__name__)

# Warnings are de-duplicated per process: `default_embedding_provider()` and the
# hash provider sit on hot paths (every retrieval run), and a warning per request
# would bury the signal it is meant to carry.
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Single-text embedding. Kept because callers and tests inject it directly."""

    def embed(self, text: str) -> list[float] | None:
        ...


@runtime_checkable
class BatchEmbeddingProvider(Protocol):
    """Batch embedding, with an async variant that does not block the event loop.

    `aembed_batch` is the method the request path must use. A provider that only
    implements the synchronous pair is wrapped by `as_async_provider`, which moves
    the call onto a worker thread rather than blocking the loop.
    """

    def embed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        ...

    async def aembed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        ...


class HashEmbeddingProvider:
    """Deterministic bag-of-tokens stand-in with no semantic content.

    Test/offline use only. Cosine similarity between these vectors is noise, so it
    must never back production retrieval; the resolver only returns it when
    `ALLOW_HASH_EMBEDDINGS` is set, and constructing it warns either way.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        _warn_once(
            "hash_embeddings",
            "HashEmbeddingProvider is a test/offline stand-in: its vectors carry no "
            "semantic signal, so retrieval that fuses them is not hybrid retrieval. "
            "Configure EMBEDDING_API_KEY for a real dense channel.",
        )
        self.dimensions = dimensions or settings.embedding_dimension

    def embed(self, text: str) -> list[float]:
        if not text:
            return [0.0] * self.dimensions
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            return [value / norm for value in vector]
        return vector

    def embed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        return [self.embed(text) for text in texts]

    async def aembed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        # Purely local arithmetic; no I/O to move off the loop.
        return self.embed_batch(texts)


class HttpEmbeddingProvider:
    """OpenAI-compatible `/embeddings` client, batched and async-capable."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        batch_size: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.embedding_api_key
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.model = model or settings.embedding_model
        self.batch_size = max(1, batch_size or settings.embedding_batch_size)
        self.timeout = timeout or settings.llm_timeout_seconds

    def _url(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _order(response_json: dict[str, Any], expected: int) -> list[list[float] | None]:
        """Map a response back to input order by `index`, not by list position.

        Providers are not required to return embeddings in request order, and the
        OpenAI-compatible contract carries an explicit `index` for exactly this
        reason. Zipping positionally would silently attach the wrong vector to the
        wrong chunk — a corruption that shows up as slightly-worse retrieval, never
        as an error.
        """
        slots: list[list[float] | None] = [None] * expected
        for item in response_json.get("data") or []:
            index = item.get("index")
            embedding = item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < expected:
                continue
            if isinstance(embedding, list) and embedding:
                slots[index] = [float(value) for value in embedding]
        return slots

    def embed(self, text: str) -> list[float] | None:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        if not self.api_key:
            return [None] * len(texts)
        results: list[list[float] | None] = []
        for start in range(0, len(texts), self.batch_size):
            window = list(texts[start : start + self.batch_size])
            results.extend(self._embed_window(window))
        return results

    def _embed_window(self, window: list[str]) -> list[list[float] | None]:
        if not window:
            return []
        try:
            response = httpx.post(
                self._url(),
                headers=self._headers(),
                json={"model": self.model, "input": window},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self._order(response.json(), len(window))
        except Exception:
            # Never raise into the retrieval path: the caller treats `None` slots as
            # "this chunk has no vector" and keeps the lexical channels.
            logger.warning("Embedding request failed for %d text(s)", len(window), exc_info=True)
            return [None] * len(window)

    async def aembed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        if not self.api_key:
            return [None] * len(texts)
        results: list[list[float] | None] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(texts), self.batch_size):
                window = list(texts[start : start + self.batch_size])
                results.extend(await self._aembed_window(client, window))
        return results

    async def _aembed_window(
        self,
        client: httpx.AsyncClient,
        window: list[str],
    ) -> list[list[float] | None]:
        if not window:
            return []
        try:
            response = await client.post(
                self._url(),
                headers=self._headers(),
                json={"model": self.model, "input": window},
            )
            response.raise_for_status()
            return self._order(response.json(), len(window))
        except Exception:
            logger.warning(
                "Async embedding request failed for %d text(s)", len(window), exc_info=True
            )
            return [None] * len(window)


class _AsyncWrapper:
    """Adapt a synchronous provider so the request path never blocks the loop."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def embed(self, text: str) -> list[float] | None:
        return self._provider.embed(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        return _sync_batch(self._provider, texts)

    async def aembed_batch(self, texts: Sequence[str]) -> list[list[float] | None]:
        return await asyncio.to_thread(_sync_batch, self._provider, texts)


def _sync_batch(provider: Any, texts: Sequence[str]) -> list[list[float] | None]:
    """Call the best batch method a provider actually offers."""
    batch = getattr(provider, "embed_batch", None)
    if callable(batch):
        return list(batch(texts))
    return [provider.embed(text) for text in texts]


def as_async_provider(provider: Any) -> Any:
    """Guarantee `aembed_batch` without changing what the provider computes."""
    if provider is None:
        return None
    if hasattr(provider, "aembed_batch"):
        return provider
    return _AsyncWrapper(provider)


def embed_many(
    texts: list[str],
    provider: Any,
) -> list[list[float] | None]:
    """Batch helper kept for callers that want the synchronous path."""
    return _sync_batch(provider, texts)


async def aembed_many(
    texts: Sequence[str],
    provider: Any,
) -> list[list[float] | None]:
    """The request-path entry point: batched, and never blocking the event loop."""
    wrapped = as_async_provider(provider)
    if wrapped is None:
        return [None] * len(texts)
    return await wrapped.aembed_batch(texts)


def default_embedding_provider() -> EmbeddingProvider | None:
    """Resolve the dense provider from configuration, or `None` when there is none.

    Returning `None` rather than a hash stand-in is the whole point: retrieval then
    runs lexical-only and reports that mode, instead of fusing vectors that cannot
    represent meaning.
    """
    if settings.embedding_api_key:
        return HttpEmbeddingProvider()
    if settings.allow_hash_embeddings:
        return HashEmbeddingProvider()
    _warn_once(
        "no_dense_provider",
        "No embedding provider is configured (EMBEDDING_API_KEY is empty): "
        "retrieval runs lexical-only and the dense channel is disabled. Set "
        "ALLOW_HASH_EMBEDDINGS=true for offline development only.",
    )
    return None


def effective_retrieval_mode(provider: Any | None) -> str:
    """The mode a run actually executes in, given the provider it resolved."""
    return RETRIEVAL_MODE_HYBRID if provider is not None else RETRIEVAL_MODE_LEXICAL_ONLY


def describe_dense_channel() -> dict[str, object]:
    """Operator-facing description of the dense channel for the readiness payload.

    `mode` is what a request will really do, so it accounts for the wiring as well
    as the credentials: the graph only asks for a provider when the LLM or Postgres
    retrieval path is enabled, and a configured key on a deployment that never asks
    for a provider is not hybrid retrieval. Claiming otherwise is exactly the
    overclaim this reporting exists to stop.
    """
    wired = settings.retrieval_use_llm or settings.retrieval_use_postgres
    if not wired:
        provider: str | None = None
    elif settings.embedding_api_key:
        provider = "http"
    elif settings.allow_hash_embeddings:
        provider = "hash"
    else:
        provider = None
    return {
        "mode": RETRIEVAL_MODE_HYBRID if provider else RETRIEVAL_MODE_LEXICAL_ONLY,
        "provider": provider,
        "configured": bool(settings.embedding_api_key),
        "wired": wired,
        "hash_embeddings_enabled": bool(settings.allow_hash_embeddings),
        "model": settings.embedding_model if provider == "http" else None,
        "batch_size": settings.embedding_batch_size if provider == "http" else None,
    }


def dense_cosine_similarity(
    left: list[float],
    right: list[float],
) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
