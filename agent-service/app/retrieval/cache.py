"""Content-addressed embedding cache.

Embedding the same text twice is the normal case, not the exception: every query
re-embeds its own text, every unchanged document is re-seeded on start-up, and the
dense channel needs vectors for candidates that were already embedded at ingest.
Without a cache each of those is an HTTP round trip.

The key is `sha256(text) + model + dimension`. **The model and dimension are part of
the key on purpose**: reusing vectors across embedding models would not raise an
error anywhere — it would silently produce meaningless cosine scores, which is a
ranking corruption that looks like "retrieval got a bit worse". A model swap must
therefore miss the cache and re-embed, not hit it and lie.

The cache stores `None` for texts that failed to embed, so a failing provider is not
retried once per chunk per query.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any, Sequence

from app.config import settings

#: A vector, or `None` for "this text could not be embedded".
CacheValue = "list[float] | None"


def cache_key(text: str, model: str, dimension: int) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{digest}:{model}:{dimension}"


def provider_identity(provider: Any, fallback: str) -> str:
    """A stable discriminator for *which* embedder produced a vector.

    Two providers can both leave `model` unset and therefore fall back to the same
    configured model name while producing entirely different vectors (or none at
    all). Keying the cache on the model name alone then lets one provider's result
    — including a cached failure — be served for another, which shows up as the
    dense channel mysteriously returning nothing. The class name is coarse but
    exact for this purpose: it never conflates two different implementations.
    """
    name = type(provider).__name__
    return f"{fallback}|{name}"


class EmbeddingCache:
    """Small thread-safe LRU. Per-process: the win is within a replica's own traffic."""

    def __init__(self, max_size: int | None = None) -> None:
        self.max_size = max(1, max_size or settings.embedding_cache_size)
        self._entries: OrderedDict[str, list[float] | None] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[bool, list[float] | None]:
        """Return `(found, value)`. `found` distinguishes a cached miss from an absent key."""
        with self._lock:
            if key not in self._entries:
                self.misses += 1
                return False, None
            value = self._entries.pop(key)
            self._entries[key] = value
            self.hits += 1
            return True, value

    def put(self, key: str, value: list[float] | None) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._entries),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else None,
            }


_cache: EmbeddingCache | None = None


def default_embedding_cache() -> EmbeddingCache:
    global _cache
    if _cache is None:
        _cache = EmbeddingCache()
    return _cache


def reset_embedding_cache() -> None:
    """Drop the process-wide cache (tests, and the operator refresh endpoint)."""
    global _cache
    _cache = None


def embed_with_cache(
    texts: Sequence[str],
    provider: Any,
    *,
    model: str | None = None,
    dimension: int | None = None,
    cache: EmbeddingCache | None = None,
) -> list[list[float] | None]:
    """Synchronous, batched, cached. Used by ingest and admin paths."""
    from app.retrieval.embedding import embed_many

    texts, store, keys, pending, results = _plan(
        texts, provider, model, dimension, cache
    )
    if pending:
        fresh = embed_many([texts[position] for position in pending], provider)
        _record(results, keys, store, texts, pending, fresh)
    return results


async def aembed_with_cache(
    texts: Sequence[str],
    provider: Any,
    *,
    model: str | None = None,
    dimension: int | None = None,
    cache: EmbeddingCache | None = None,
) -> list[list[float] | None]:
    """Async, batched, cached. Used by the request path."""
    from app.retrieval.embedding import aembed_many

    texts, store, keys, pending, results = _plan(
        texts, provider, model, dimension, cache
    )
    if pending:
        fresh = await aembed_many([texts[position] for position in pending], provider)
        _record(results, keys, store, texts, pending, fresh)
    return results


def _plan(
    texts: Sequence[str],
    provider: Any,
    model: str | None,
    dimension: int | None,
    cache: EmbeddingCache | None,
) -> tuple[
    list[str],
    EmbeddingCache | None,
    list[str],
    list[int],
    list[list[float] | None],
]:
    """Resolve cache keys and split the request into hits and misses."""
    resolved = list(texts)
    if provider is None:
        return resolved, None, [], [], [None] * len(resolved)

    resolved_model = model or getattr(provider, "model", None) or settings.embedding_model
    resolved_dimension = dimension or int(
        getattr(provider, "dimensions", None) or settings.embedding_dimension
    )
    # Provider identity is part of the key: see `provider_identity` for why the
    # model name alone is not enough.
    identity = provider_identity(provider, resolved_model)
    store = cache or (default_embedding_cache() if settings.embedding_cache_enabled else None)

    keys = [
        cache_key(text, identity, resolved_dimension) for text in resolved
    ]
    results: list[list[float] | None] = [None] * len(resolved)
    pending: list[int] = []

    for position, key in enumerate(keys):
        if store is None:
            pending.append(position)
            continue
        found, value = store.get(key)
        if found:
            results[position] = value
        else:
            pending.append(position)

    return resolved, store, keys, pending, results


def _record(
    results: list[list[float] | None],
    keys: list[str],
    store: EmbeddingCache | None,
    texts: list[str],
    pending: list[int],
    fresh: Sequence[list[float] | None],
) -> None:
    for slot, value in zip(pending, fresh):
        results[slot] = value
        if store is not None:
            store.put(keys[slot], value)

