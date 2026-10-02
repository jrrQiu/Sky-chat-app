from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.knowledge.corpus import POLICY_DOCUMENTS, TICKET_CASES
from app.retrieval.pipeline import run_retrieval


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    content: str
    keywords: list[str]
    allowed_roles: list[str] = field(default_factory=list)


def _to_legacy_document(
    evidence: dict[str, Any],
) -> KnowledgeDocument:
    metadata = evidence.get("metadata", {})
    return KnowledgeDocument(
        id=evidence.get("source_id", ""),
        title=evidence.get("title", ""),
        content=evidence.get("content", ""),
        keywords=list(metadata.get("keywords", [])),
        allowed_roles=list(metadata.get("allowed_roles", [])),
    )


DOCUMENTS: list[KnowledgeDocument] = [
    KnowledgeDocument(
        id=document.id,
        title=document.title,
        content=document.content,
        keywords=list(document.keywords),
        allowed_roles=list(document.allowed_roles),
    )
    for document in POLICY_DOCUMENTS
    if document.status == "active"
]

HISTORICAL_TICKETS: list[KnowledgeDocument] = [
    KnowledgeDocument(
        id=ticket.id,
        title=ticket.symptom,
        content=ticket.symptom,
        keywords=list(ticket.keywords),
        allowed_roles=list(ticket.allowed_roles),
    )
    for ticket in TICKET_CASES
]


def search_knowledge(
    query: str,
    user_context: dict[str, Any] | None = None,
    limit: int = 3,
) -> list[KnowledgeDocument]:
    result = run_retrieval(query, user_context=user_context)
    documents = [
        _to_legacy_document(item)
        for item in result.evidence
        if item.get("source_type") == "policy"
    ]
    return documents[:limit]


def search_historical_tickets(
    query: str,
    user_context: dict[str, Any] | None = None,
    limit: int = 2,
) -> list[KnowledgeDocument]:
    result = run_retrieval(query, user_context=user_context)
    documents = [
        _to_legacy_document(item)
        for item in result.evidence
        if item.get("source_type") == "ticket"
    ]
    return documents[:limit]


def format_knowledge_context(
    query: str,
    include_history: bool = False,
    user_context: dict[str, Any] | None = None,
) -> str:
    result = run_retrieval(query, user_context=user_context)
    if include_history:
        return result.context

    policy_evidence = [
        item for item in result.evidence if item.get("source_type") == "policy"
    ]
    if not policy_evidence:
        return "未检索到完全匹配的制度或历史工单。"

    blocks = ["【制度与 SOP】"]
    for index, item in enumerate(policy_evidence, start=1):
        metadata = item.get("metadata", {})
        blocks.append(
            f"[{index}] {item['title']}（{metadata.get('citation', item['source_id'])}）\n"
            f"{item['content']}"
        )
    return "\n".join(blocks)
