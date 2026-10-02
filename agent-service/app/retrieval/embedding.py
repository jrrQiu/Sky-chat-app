from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

import httpx

from app.config import settings


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int | None = None) -> None:
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


def default_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_api_key:
        return HttpEmbeddingProvider()
    return HashEmbeddingProvider()


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
