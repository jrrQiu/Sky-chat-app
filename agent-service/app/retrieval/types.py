from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.knowledge.models import (
    KnowledgeBase,
    KnowledgeChunk,
    PolicyDocument,
    TicketCase,
    TicketEvent,
)


@dataclass
class RetrievalIndex:
    knowledge_bases: list[KnowledgeBase]
    policy_documents: list[PolicyDocument]
    chunks: list[KnowledgeChunk]
    tickets: list[TicketCase]
    events: list[TicketEvent]

    def kb_by_id(self, kb_id: str) -> KnowledgeBase | None:
        return next((kb for kb in self.knowledge_bases if kb.id == kb_id), None)

    def document_by_id(self, document_id: str) -> PolicyDocument | None:
        return next(
            (document for document in self.policy_documents if document.id == document_id),
            None,
        )

    def chunks_by_document(self, document_id: str) -> list[KnowledgeChunk]:
        return [chunk for chunk in self.chunks if chunk.document_id == document_id]

    def ticket_by_id(self, ticket_id: str) -> TicketCase | None:
        return next((ticket for ticket in self.tickets if ticket.id == ticket_id), None)

    def events_by_ticket(self, ticket_id: str) -> list[TicketEvent]:
        return [event for event in self.events if event.case_id == ticket_id]


@dataclass
class ScoredItem:
    item: Any
    score: float
    matched_by: str = ""
    rank: int = 0


@dataclass
class RetrievalResult:
    task_type: str
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    context: str
    tool_events: list[dict[str, Any]]
    issues: list[str] = field(default_factory=list)
    # Evidence quarantined by the retrieval rail (untrusted-content screening).
    rail_blocked: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "plan": self.plan,
            "evidence": self.evidence,
            "documents": self.documents,
            "context": self.context,
            "tool_events": self.tool_events,
            "issues": self.issues,
            "rail_blocked": self.rail_blocked,
        }
