from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.knowledge.text import cosine_similarity, sparse_vector
from app.retrieval.rerank import rerank_ticket_candidates
from app.retrieval.types import RetrievalIndex, ScoredItem


def _can_access_ticket(ticket: Any, user_context: dict[str, Any] | None) -> bool:
    if not ticket.allowed_roles:
        return True
    if not user_context:
        return False
    roles = set(user_context.get("roles", []))
    return bool(roles.intersection(ticket.allowed_roles))


def _recency_decay(closed_at: str | None) -> float:
    if not closed_at:
        return 0.5
    try:
        closed = datetime.fromisoformat(closed_at).date()
    except ValueError:
        return 0.5
    days = max((date.today() - closed).days, 0)
    return max(0.2, 1.0 - days / 365.0)


def search_similar_tickets(
    index: RetrievalIndex,
    signature: dict[str, Any],
    query: str,
    user_context: dict[str, Any] | None = None,
    top_k: int = 3,
) -> list[ScoredItem]:
    query_vector = sparse_vector(query)
    ranked: list[ScoredItem] = []
    signature_errors = {
        code.lower() for code in signature.get("error_codes", [])
    }

    for ticket in index.tickets:
        if not _can_access_ticket(ticket, user_context):
            continue

        text = " ".join(
            [
                ticket.product,
                ticket.component,
                ticket.environment,
                ticket.symptom,
                ticket.root_cause,
                ticket.resolution,
            ]
        )
        similarity = cosine_similarity(query_vector, sparse_vector(text))
        exact_hits = 0
        for code in signature_errors:
            if code in {item.lower() for item in ticket.error_codes}:
                exact_hits += 8
        if signature.get("product") and signature["product"].lower() in ticket.product.lower():
            exact_hits += 3
        if signature.get("component") and signature["component"].lower() in ticket.component.lower():
            exact_hits += 2

        matched_by = []
        if exact_hits:
            matched_by.append("exact")
        if similarity > 0:
            matched_by.append("vector")
        score = similarity * 3 + exact_hits + _recency_decay(ticket.closed_at) * 0.3
        if score <= 0:
            continue
        ranked.append(
            ScoredItem(
                item=ticket,
                score=score,
                matched_by=",".join(matched_by),
            )
        )

    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    return rerank_ticket_candidates(ranked[:top_k], query)


def ticket_text(ticket: Any) -> str:
    fields = [
        f"产品：{ticket.product}",
        f"组件：{ticket.component}",
        f"环境：{ticket.environment}",
        f"状态：{ticket.status}",
        f"现象：{ticket.symptom}",
    ]
    if ticket.error_codes:
        fields.append(f"错误码：{', '.join(ticket.error_codes)}")
    if ticket.root_cause:
        fields.append(f"根因：{ticket.root_cause}")
    if ticket.resolution:
        fields.append(f"解决方案：{ticket.resolution}")
    return "\n".join(fields)
