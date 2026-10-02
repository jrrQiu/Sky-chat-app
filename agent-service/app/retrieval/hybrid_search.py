"""Hybrid retrieval over policy chunks.

Two lexical channels (BM25 and sparse cosine) and one exact-match channel always
run; the dense channel is *added* on top when a real embedding provider is
available. Treating dense as additive is deliberate: it used to replace the sparse
channel even when the "dense" provider was a hash stand-in, which fused noise into
the ranking, and a provider that fails mid-run must not be able to empty the result
set either.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict

from app.knowledge.models import KnowledgeChunk
from app.knowledge.text import cosine_similarity, sparse_vector, tokenize
from app.retrieval.embedding import dense_cosine_similarity
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.rerank import rerank_policy_chunks
from app.retrieval.types import RetrievalIndex, ScoredItem

logger = logging.getLogger(__name__)


def _document_frequency(chunks: list[KnowledgeChunk]) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for chunk in chunks:
        frequency.update(set(tokenize(chunk.content)))
    return frequency


def bm25_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    top_k: int,
) -> list[ScoredItem]:
    if not chunks:
        return []

    query_terms = tokenize(query)
    document_frequency = _document_frequency(chunks)
    total_documents = len(chunks)
    average_length = sum(len(tokenize(chunk.content)) for chunk in chunks) / total_documents
    k1 = 1.2
    b = 0.75

    ranked: list[ScoredItem] = []
    for chunk in chunks:
        terms = tokenize(chunk.content)
        term_frequency = Counter(terms)
        document_length = len(terms)
        score = 0.0
        for term in set(query_terms):
            term_frequency_value = term_frequency.get(term, 0)
            if term_frequency_value == 0:
                continue
            inverse_document_frequency = math.log(
                1
                + (total_documents - document_frequency.get(term, 0) + 0.5)
                / (document_frequency.get(term, 0) + 0.5)
            )
            denominator = term_frequency_value + k1 * (
                1 - b + b * document_length / max(average_length, 1)
            )
            score += inverse_document_frequency * term_frequency_value * (k1 + 1) / denominator

        if score > 0:
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="bm25"))

    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked[:top_k]


def dense_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    top_k: int,
    embedding_provider,
) -> list[ScoredItem]:
    """Dense (semantic) channel, or nothing when it cannot contribute.

    An empty return means "no dense results", never "no results": the caller keeps
    the lexical channels, so a provider that is absent, misconfigured or failing
    degrades the run instead of wiping it out.
    """
    if embedding_provider is None:
        return []

    query_embedding = embedding_provider.embed(query)
    if not query_embedding:
        logger.warning(
            "Dense embedding provider returned no vector for the query; "
            "this retrieval run is lexical-only"
        )
        return []

    ranked: list[ScoredItem] = []
    for chunk in chunks:
        chunk_embedding = embedding_provider.embed(chunk.content)
        if not chunk_embedding:
            continue
        score = dense_cosine_similarity(query_embedding, chunk_embedding)
        if score > 0:
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="vector"))

    if not ranked:
        logger.warning(
            "Dense channel produced no candidate above zero similarity; "
            "this retrieval run is lexical-only"
        )
    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked[:top_k]


def sparse_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    top_k: int,
) -> list[ScoredItem]:
    """Lexical sparse-cosine channel (bag-of-words overlap).

    Labelled `sparse`, not `vector`: it has no embedding behind it, and calling it
    a vector channel is what made a lexical-only run look like a dense one.
    """
    query_vector = sparse_vector(query)
    ranked: list[ScoredItem] = []
    for chunk in chunks:
        score = cosine_similarity(query_vector, sparse_vector(chunk.content))
        if score > 0:
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="sparse"))

    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked[:top_k]


def exact_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    top_k: int,
) -> list[ScoredItem]:
    normalized_query = query.lower()
    terms = tokenize(query)
    ranked: list[ScoredItem] = []

    for chunk in chunks:
        content = chunk.content.lower()
        keyword_hits = sum(
            1 for keyword in chunk.keywords if keyword.lower() in normalized_query
        )
        term_hits = sum(1 for term in terms if term in content)
        title_hits = sum(1 for term in terms if term in " ".join(chunk.section_path).lower())
        score = float(keyword_hits * 5 + title_hits * 8 + term_hits)
        if score > 0:
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="exact"))

    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return ranked[:top_k]


def hybrid_retrieve_chunks(
    query: str,
    index: RetrievalIndex,
    kb_ids: set[str],
    *,
    include_archived: bool = False,
    top_k: int = 8,
    rerank_client=None,
    embedding_provider=None,
) -> list[ScoredItem]:
    allowed_documents = {
        document.id
        for document in index.policy_documents
        if document.kb_id in kb_ids
        and (include_archived or document.status == "active")
    }
    chunks = [
        chunk for chunk in index.chunks if chunk.document_id in allowed_documents
    ]
    if not chunks:
        return []

    ranked_lists = [
        bm25_rank(query, chunks, top_k=50),
        sparse_rank(query, chunks, top_k=50),
        exact_rank(query, chunks, top_k=20),
    ]
    # Additive: with no provider, or one that yields nothing, the fusion below just
    # sees an empty dense list and still ranks the lexical candidates.
    if embedding_provider is not None:
        ranked_lists.append(
            dense_rank(query, chunks, top_k=50, embedding_provider=embedding_provider)
        )

    fused = reciprocal_rank_fusion(ranked_lists, k=60)
    document_map = {
        document.id: document for document in index.policy_documents
    }
    reranked = rerank_policy_chunks(
        fused,
        query,
        document_map,
        client=rerank_client,
    )
    return reranked[:top_k]
