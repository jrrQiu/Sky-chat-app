from __future__ import annotations

from app.knowledge.models import TicketEvent
from app.retrieval.types import RetrievalIndex


def get_timeline(index: RetrievalIndex, ticket_id: str) -> list[TicketEvent]:
    events = index.events_by_ticket(ticket_id)
    return sorted(events, key=lambda event: event.occurred_at)


def format_timeline(events: list[TicketEvent]) -> str:
    if not events:
        return "无时间线记录。"

    labels = {
        "created": "创建",
        "rejected": "退回",
        "resubmitted": "重新提交",
        "approved": "审批通过",
        "resolved": "解决",
        "commented": "评论",
        "changed": "变更",
    }
    lines = []
    for event in events:
        label = labels.get(event.event_type, event.event_type)
        lines.append(f"{event.occurred_at} [{label}] {event.content}")
    return "\n".join(lines)
