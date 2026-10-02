from __future__ import annotations

from datetime import date
from typing import Any

from app.knowledge.models import KnowledgeChunk, PolicyDocument
from app.retrieval.llm import LLMStructuredClient
from app.retrieval.types import ScoredItem


def _phrase_overlap(query: str, text: str) -> float:
    query_tokens = set(query.lower().split())
    text_tokens = set(text.lower().split())
    overlap = query_tokens & text_tokens
    return len(overlap) / max(len(query_tokens), 1)


def rerank_policy_chunks(
    candidates: list[ScoredItem],
    query: str,
    documents: dict[str, PolicyDocument],
    client: LLMStructuredClient | Any | None = None,
) -> list[ScoredItem]:
    reranked: list[ScoredItem] = []
    today = date.today().isoformat()

    for candidate in candidates:
        chunk: KnowledgeChunk = candidate.item
        document = documents.get(chunk.document_id)
        if not document:
            reranked.append(candidate)
            continue

        bonus = 0.0
        if document.authority_level >= 95:
            bonus += 0.08
        if document.status == "active":
            bonus += 0.05
        if document.expires_at and document.expires_at < today:
            bonus -= 0.12
        if "root" in chunk.id.lower() or chunk.parent_chunk_id is None:
            bonus += 0.02
        bonus += _phrase_overlap(query, chunk.content) * 0.04
        bonus += _phrase_overlap(query, " ".join(chunk.section_path)) * 0.12

        candidate.score += bonus
        reranked.append(candidate)

    reranked.sort(key=lambda candidate: candidate.score, reverse=True)
    for rank, candidate in enumerate(reranked, start=1):
        candidate.rank = rank
    if client is not None:
        reranked = _model_rerank(reranked, query, client)
    return reranked


def _model_rerank(
    candidates: list[ScoredItem],
    query: str,
    client: LLMStructuredClient | Any,
) -> list[ScoredItem]:
    raw = client.complete_json(
        system=(
            "你是检索重排器。只输出 JSON：{\"ranking\": [\"chunk_id\", ...]}。"
            "按与用户问题的相关性从高到低排列候选 chunk id。"
        ),
        user=(
            f"用户问题：{query}\n"
            "候选 chunk：\n"
            + "\n".join(
                f"{candidate.item.id}: {candidate.item.content[:120]}"
                for candidate in candidates
            )
        ),
    )
    if not isinstance(raw, dict):
        return candidates

    ranking = raw.get("ranking")
    if not isinstance(ranking, list):
        return candidates

    by_id = {candidate.item.id: candidate for candidate in candidates}
    ordered = [by_id[item_id] for item_id in ranking if item_id in by_id]
    remaining = [candidate for candidate in candidates if candidate.item.id not in set(ranking)]
    return ordered + remaining


def rerank_ticket_candidates(
    candidates: list[ScoredItem],
    query: str,
) -> list[ScoredItem]:
    reranked: list[ScoredItem] = []
    for candidate in candidates:
        ticket = candidate.item
        bonus = _phrase_overlap(query, ticket.symptom) * 0.08
        if ticket.status == "resolved":
            bonus += 0.03
        candidate.score += bonus
        reranked.append(candidate)

    reranked.sort(key=lambda candidate: candidate.score, reverse=True)
    for rank, candidate in enumerate(reranked, start=1):
        candidate.rank = rank
    return reranked
