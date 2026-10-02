from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.knowledge.models import KnowledgeChunk
from app.knowledge.text import cosine_similarity, sparse_vector, tokenize
from app.retrieval.embedding import dense_cosine_similarity
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.rerank import rerank_policy_chunks
from app.retrieval.types import RetrievalIndex, ScoredItem


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


def vector_rank(
    query: str,
    chunks: list[KnowledgeChunk],
    top_k: int,
    embedding_provider=None,
) -> list[ScoredItem]:
    if embedding_provider is not None:
        query_embedding = embedding_provider.embed(query)
        if query_embedding:
            ranked: list[ScoredItem] = []
            for chunk in chunks:
                chunk_embedding = embedding_provider.embed(chunk.content)
                if not chunk_embedding:
                    continue
                score = dense_cosine_similarity(query_embedding, chunk_embedding)
                if score > 0:
                    ranked.append(ScoredItem(item=chunk, score=score, matched_by="vector"))
            ranked.sort(key=lambda candidate: candidate.score, reverse=True)
            return ranked[:top_k]

    query_vector = sparse_vector(query)
    ranked: list[ScoredItem] = []
    for chunk in chunks:
        score = cosine_similarity(query_vector, sparse_vector(chunk.content))
        if score > 0:
            ranked.append(ScoredItem(item=chunk, score=score, matched_by="vector"))

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

    bm25_candidates = bm25_rank(query, chunks, top_k=50)
    vector_candidates = vector_rank(
        query,
        chunks,
        top_k=50,
        embedding_provider=embedding_provider,
    )
    exact_candidates = exact_rank(query, chunks, top_k=20)

    fused = reciprocal_rank_fusion(
        [bm25_candidates, vector_candidates, exact_candidates],
        k=60,
    )
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
