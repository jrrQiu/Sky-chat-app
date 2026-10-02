from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class IntentResolution:
    domain: str
    operation: str
    intent: str
    risk: str
    confidence: float
    source: str
    agent_id: str
    action: str
    plan: list[str]
    entities: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    requires_clarification: bool = False
    clarifying_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def legacy_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "agent_id": self.agent_id,
            "action": self.action,
            "risk_level": self.risk,
            "plan": self.plan,
            "domain": self.domain,
            "operation": self.operation,
            "confidence": self.confidence,
            "source": self.source,
            "entities": self.entities,
            "slots": self.slots,
            "missing_fields": self.missing_slots,
            "requires_clarification": self.requires_clarification,
            "clarifying_question": self.clarifying_question,
        }
