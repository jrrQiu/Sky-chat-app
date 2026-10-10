"""The context report: what the engine decided, and what it cost.

The report exists because "the context was trimmed to fit" is not an answer an
operator or an auditor can act on. Every drop and every truncation is counted and
**named**: which message ids left the prompt, which evidence ids were dropped,
which block was cut and by how much, and where the omissions were handed to the
summary so the range is contiguous.

Two fields describe the same thing from opposite directions:

``usable_tokens``/``used_tokens``
    What the engine planned against.
``provider_usage``/``estimate_error_ratio``
    What the provider actually charged, filled in after the call. A counter that
    is systematically wrong (a vendor changing its tokenizer) is only visible
    here, which is why the estimate is calibrated rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Bumped whenever the report's shape changes, so a consumer can refuse to guess.
ENGINE_VERSION = 2

#: |estimate_error_ratio| above this is worth an alert: it means the tokenizer no
#: longer matches the model.
CALIBRATION_ALERT_THRESHOLD = 0.25


def estimate_error_ratio(
    estimated_input_tokens: int | None,
    provider_input_tokens: int | None,
) -> float | None:
    """Relative error of our count against the provider's, or ``None``.

    ``None`` rather than ``0.0`` when either side is unknown: a missing
    measurement is not a perfect one.
    """
    if not provider_input_tokens:
        return None
    if estimated_input_tokens is None:
        return None
    return (provider_input_tokens - estimated_input_tokens) / provider_input_tokens


@dataclass
class BlockReport:
    kind: str
    priority: int
    protection: str
    budget: int
    actual: int
    truncated: bool = False
    dropped: bool = False
    omitted_tokens: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    citation_map: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: True when the block's content is a list packed whole-item at a time.
    count_by: str = "tokens"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "priority": self.priority,
            "protection": self.protection,
            "budget": self.budget,
            "actual": self.actual,
            "truncated": self.truncated,
            "count_by": self.count_by,
        }
        if self.dropped:
            payload["dropped"] = True
        if self.omitted_tokens:
            payload["omitted_tokens"] = self.omitted_tokens
        if self.dropped_ids:
            payload["dropped_ids"] = list(self.dropped_ids)
        if self.citation_map:
            payload["citation_map"] = dict(self.citation_map)
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload


@dataclass
class MessagesReport:
    """What happened to the conversation history, in counts and in ids."""

    budget: int = 0
    kept: int = 0
    dropped: int = 0
    tokens: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    truncated_ids: list[str] = field(default_factory=list)
    omitted_tokens: int = 0
    #: Ids of messages that left the prompt; the summary must cover exactly these.
    evicted_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "kept": self.kept,
            "dropped": self.dropped,
            "tokens": self.tokens,
            "dropped_ids": list(self.dropped_ids),
            "truncated_ids": list(self.truncated_ids),
            "omitted_tokens": self.omitted_tokens,
            "evicted_ids": list(self.evicted_ids),
        }


@dataclass
class EvidenceReport:
    budget: int = 0
    kept: int = 0
    dropped: int = 0
    tokens: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    citation_map: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "kept": self.kept,
            "dropped": self.dropped,
            "tokens": self.tokens,
            "dropped_ids": list(self.dropped_ids),
            "citation_map": dict(self.citation_map),
        }


@dataclass
class SummaryRequest:
    """What the summary stage needs, decided at allocation time.

    The planner knows *which* messages were evicted; producing the summary needs
    the model and therefore an await, so the two are separate stages. Keeping the
    range here is what makes "summary + originals" cover the conversation with no
    gap and no overlap.
    """

    covered_from_message_id: str = ""
    covered_until_message_id: str = ""
    previous_version: int = 0
    message_count: int = 0
    reason: str = "messages_evicted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "pending",
            "covered_from_message_id": self.covered_from_message_id,
            "covered_until_message_id": self.covered_until_message_id,
            "previous_version": self.previous_version,
            "message_count": self.message_count,
            "reason": self.reason,
        }


@dataclass
class SummaryDelta:
    """A summary revision covering a contiguous range of evicted messages."""

    covered_from_message_id: str = ""
    covered_until_message_id: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    previous_version: int = 0
    text: str = ""
    quality: str = "ok"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered_from_message_id": self.covered_from_message_id,
            "covered_until_message_id": self.covered_until_message_id,
            "fields": self.fields,
            "previous_version": self.previous_version,
            "text": self.text,
            "quality": self.quality,
            "reason": self.reason,
        }


@dataclass
class ContextReport:
    """The full record of one context assembly."""

    engine_version: int = ENGINE_VERSION
    model: str = ""
    counter: dict[str, Any] = field(default_factory=dict)
    window: int = 0
    max_output: int = 0
    safety_margin: int = 0
    usable_tokens: int = 0
    used_tokens: int = 0
    scaffolding_tokens: int = 0
    blocks: list[BlockReport] = field(default_factory=list)
    messages: MessagesReport = field(default_factory=MessagesReport)
    evidence: EvidenceReport = field(default_factory=EvidenceReport)
    infeasible: bool = False
    infeasible_reason: str = ""
    missing_tokens: int = 0
    needs_summary: bool = False
    summary_request: SummaryRequest | None = None
    summary_delta: SummaryDelta | None = None
    provider_usage: dict[str, Any] | None = None
    estimate_error_ratio: float | None = None
    #: What we measured before the call; `provider_usage` is what it actually cost.
    estimated_input_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def utilization(self) -> float:
        if not self.usable_tokens:
            return 0.0
        return min(1.0, self.used_tokens / self.usable_tokens)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "engine_version": self.engine_version,
            "model": self.model,
            "counter": dict(self.counter),
            "window": self.window,
            "max_output": self.max_output,
            "safety_margin": self.safety_margin,
            "usable_tokens": self.usable_tokens,
            "used_tokens": self.used_tokens,
            "scaffolding_tokens": self.scaffolding_tokens,
            "utilization": round(self.utilization, 6),
            "blocks": [block.to_dict() for block in self.blocks],
            "messages": self.messages.to_dict(),
            "evidence": self.evidence.to_dict(),
            "infeasible": self.infeasible,
            "needs_summary": self.needs_summary,
        }
        if self.infeasible_reason:
            payload["infeasible_reason"] = self.infeasible_reason
        if self.missing_tokens:
            payload["missing_tokens"] = self.missing_tokens
        if self.summary_request is not None:
            payload["summary_request"] = self.summary_request.to_dict()
        if self.summary_delta is not None:
            payload["summary_delta"] = self.summary_delta.to_dict()
        if self.provider_usage is not None:
            payload["provider_usage"] = dict(self.provider_usage)
        if self.estimate_error_ratio is not None:
            payload["estimate_error_ratio"] = round(self.estimate_error_ratio, 6)
        payload["estimated_input_tokens"] = self.estimated_input_tokens
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload

    def block(self, kind: str) -> BlockReport | None:
        return next((item for item in self.blocks if item.kind == kind), None)
