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
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Protocol

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


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
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


class HttpEmbeddingProvider:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.embedding_api_key
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.model = model or settings.embedding_model

    def _url(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    def embed(self, text: str) -> list[float] | None:
        if not self.api_key:
            return None
        try:
            response = httpx.post(
                self._url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": text},
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return list(payload["data"][0]["embedding"])
        except Exception:
            return None


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
    }


def embed_many(
    texts: list[str],
    provider: EmbeddingProvider,
) -> list[list[float]]:
    return [provider.embed(text) for text in texts]


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
