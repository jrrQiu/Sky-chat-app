"""Dense retrieval backends: pgvector ANN, with an in-process fallback.

The dense channel had no storage at all. `knowledge_chunk.embedding` existed in
`schema.sql`, `pgvector` was a declared dependency, and no row was ever written —
so "hybrid retrieval" either ran lexical-only, or embedded every chunk on every
query (N+1 HTTP calls) and scanned the result in Python.

Two backends implement the same `search_dense` contract:

* `PgVectorStore` — vectors were persisted at ingest; the query is a single
  `ORDER BY embedding <=> %s` against an HNSW index, with the knowledge-base and
  ACL filters pushed into the same SQL statement. The ACL filter has to live in the
  query: filtering after retrieval both leaks (the model may see a chunk the caller
  may not) and destroys recall (post-filtering a top-50 can leave two rows).
* `InMemoryDenseStore` — embeddings computed on demand over the in-process index.
  Correct, O(n) per query, and only appropriate for the small seed corpus; `/ready`
  reports which backend is live so this is never mistaken for the real thing.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, Sequence

from app.config import settings
from app.knowledge.models import KnowledgeChunk
from app.retrieval.embedding import dense_cosine_similarity
from app.retrieval.types import ScoredItem

logger = logging.getLogger(__name__)

#: `retrieval_mode` values reported when a dense backend was actually consulted.
BACKEND_PGVECTOR = "pgvector"
BACKEND_IN_MEMORY = "in_memory"
BACKEND_NONE = "none"


class DenseStore(Protocol):
    backend: str

    def search_dense(
        self,
        query: str,
        *,
        allowed_chunk_ids: set[str] | None,
        top_k: int,
        provider: Any,
    ) -> list[ScoredItem]:
        ...


class InMemoryDenseStore:
    """Backup path: embed the candidate chunks on demand.

    Kept deliberately honest rather than removed. When there is no database (tests,
    offline development) the dense channel must still be able to contribute, and
    the alternative — silently dropping it — is how a "hybrid" claim becomes a lie.
    """

    backend = BACKEND_IN_MEMORY

    def __init__(self, chunks: Sequence[KnowledgeChunk]) -> None:
        self.chunks = list(chunks)

    def search_dense(
        self,
        query: str,
        *,
        allowed_chunk_ids: set[str] | None,
        top_k: int,
        provider: Any,
    ) -> list[ScoredItem]:
        if provider is None:
            return []

        # Local import: the cache module imports `embedding`, which imports
        # `types`; a module-level import here would close that loop.
        from app.retrieval.cache import embed_with_cache

        candidates = [
            chunk
            for chunk in self.chunks
            if allowed_chunk_ids is None or chunk.id in allowed_chunk_ids
        ]
        if not candidates:
            return []

        vectors = embed_with_cache([query, *(chunk.content for chunk in candidates)], provider)
        query_vector = vectors[0]
        if not query_vector:
            logger.warning(
                "Dense embedding provider returned no vector for the query; "
                "this retrieval run is lexical-only"
            )
            return []

        ranked: list[ScoredItem] = []
        for chunk, chunk_vector in zip(candidates, vectors[1:]):
            if not chunk_vector:
                continue
            score = dense_cosine_similarity(query_vector, chunk_vector)
            if score > 0:
                ranked.append(ScoredItem(item=chunk, score=score, matched_by="vector"))

        if not ranked:
            logger.warning(
                "Dense channel produced no candidate above zero similarity; "
                "this retrieval run is lexical-only"
            )
        ranked.sort(key=lambda candidate: candidate.score, reverse=True)
        return ranked[:top_k]


class PgVectorStore:
    """Single-query ANN over `knowledge_chunk.embedding`, ACL filter pushed down.

    Accepts an optional repository so tests can inject a fake; otherwise a
    `PostgresKnowledgeRepository` against the configured database URL is used.
    """

    backend = BACKEND_PGVECTOR

    def __init__(self, repository: Any | None = None) -> None:
        self._repository = repository

    def _repo(self):
        if self._repository is not None:
            return self._repository
        from app.persistence.knowledge_repository import PostgresKnowledgeRepository

        return PostgresKnowledgeRepository()

    def search_dense(
        self,
        query: str,
        *,
        allowed_chunk_ids: set[str] | None,
        top_k: int,
        provider: Any,
    ) -> list[ScoredItem]:
        if provider is None:
            return []

        from app.retrieval.cache import embed_with_cache

        vectors = embed_with_cache([query], provider)
        query_vector = vectors[0]
        if not query_vector:
            logger.warning(
                "Dense embedding provider returned no vector for the query; "
                "this retrieval run is lexical-only"
            )
            return []

        repo = self._repo()
        try:
            rows = repo.search_chunks_by_vector(
                query_vector,
                top_k=top_k,
                chunk_ids=allowed_chunk_ids,
            )
        except Exception:
            # A vector-store failure must degrade the run, never empty it: the
            # lexical channels are still in play.
            logger.warning("pgvector search failed; dense channel skipped", exc_info=True)
            return []

        ranked: list[ScoredItem] = []
        for row in rows:
            chunk = row["chunk"]
            score = float(row["score"])
            if score <= 0:
                continue
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="vector"))
        ranked.sort(key=lambda candidate: candidate.score, reverse=True)
        return ranked[:top_k]


def build_dense_store(
    *,
    chunks: Sequence[KnowledgeChunk],
    repository: Any | None = None,
) -> DenseStore | None:
    """Pick the dense backend, preferring persisted vectors when they exist.

    Returns `None` when nothing can serve the dense channel, which is the signal
    the pipeline turns into an honest `lexical_only` mode.
    """
    if not settings.vector_store_enabled:
        return InMemoryDenseStore(chunks)

    from app.persistence.knowledge_repository import PostgresKnowledgeRepository

    repo = repository or PostgresKnowledgeRepository()
    try:
        if repo.is_available() and repo.count_chunk_embeddings() > 0:
            return PgVectorStore(repo)
    except Exception:
        logger.warning("Could not resolve the pgvector backend", exc_info=True)

    return InMemoryDenseStore(chunks)


def describe_dense_backend() -> dict[str, object]:
    """Readiness reporting: is the real ANN path live, and is it populated?"""
    if not settings.vector_store_enabled:
        return {
            "backend": BACKEND_IN_MEMORY,
            "enabled": False,
            "embedded_chunks": None,
            "ann_index": False,
        }
    from app.persistence.knowledge_repository import PostgresKnowledgeRepository

    repo = PostgresKnowledgeRepository()
    try:
        if not repo.is_available():
            return {
                "backend": BACKEND_IN_MEMORY,
                "enabled": False,
                "embedded_chunks": None,
                "ann_index": False,
                "reason": "database-unavailable",
            }
        embedded = repo.count_chunk_embeddings()
        return {
            "backend": BACKEND_PGVECTOR if embedded else BACKEND_IN_MEMORY,
            "enabled": True,
            "embedded_chunks": embedded,
            "ann_index": repo.has_ann_index(),
            "hnsw": {
                "m": settings.vector_hnsw_m,
                "ef_construction": settings.vector_hnsw_ef_construction,
                "ef_search": settings.vector_hnsw_ef_search,
            },
        }
    except Exception:
        return {
            "backend": BACKEND_IN_MEMORY,
            "enabled": False,
            "embedded_chunks": None,
            "ann_index": False,
            "reason": "probe-failed",
        }
