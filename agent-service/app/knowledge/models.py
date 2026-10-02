from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeBase:
    id: str
    name: str
    domain: str
    description: str
    keywords: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyDocument:
    id: str
    kb_id: str
    title: str
    content: str
    version: str
    authority_level: int
    effective_at: str | None
    expires_at: str | None
    status: str = "active"
    source_type: str = "markdown"
    allowed_roles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def base_key(self) -> str:
        return self.title.strip().lower()


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    document_id: str
    parent_chunk_id: str | None
    chunk_index: int
    content: str
    keywords: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    page: int = 1
    source_type: str = "markdown"
    token_count: int = 0


@dataclass(frozen=True)
class TicketCase:
    id: str
    tenant_id: str
    product: str
    component: str
    environment: str
    status: str
    symptom: str
    error_codes: list[str] = field(default_factory=list)
    root_cause: str = ""
    resolution: str = ""
    closed_at: str | None = None
    allowed_roles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    recent_change: str = ""


@dataclass(frozen=True)
class TicketEvent:
    id: str
    case_id: str
    event_type: str
    content: str
    actor_hash: str
    occurred_at: str


@dataclass(frozen=True)
class Evidence:
    id: str
    source_type: str
    source_id: str
    kb_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    matched_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "kb_id": self.kb_id,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "score": self.score,
            "matched_by": self.matched_by,
        }
