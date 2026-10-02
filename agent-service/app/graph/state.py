import operator
from typing import Annotated, Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    turn_id: str
    execution_id: str
    request_id: str
    user_id: str
    user_context: dict[str, Any]
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    messages: Annotated[list[dict[str, str]], operator.add]
    summary: str
    latest_user_message: str
    model: str | None
    api_key: str
    enable_thinking: bool
    enable_web_search: bool
    intent: str | None
    domain: str
    operation: str
    intent_confidence: float
    intent_source: str
    intent_resolution: dict[str, Any]
    route: str
    action: str
    selected_agent: str
    risk_level: str | None
    slots: dict[str, Any]
    missing_fields: list[str]
    plan: list[str]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    knowledge_documents: Annotated[list[dict[str, Any]], operator.add]
    retrieved_documents: list[Any]
    knowledge_context: str
    retrieval_plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    retrieval_issues: list[str]
    quarantined_evidence_count: int
    guardrail_events: list[dict[str, Any]]
    requires_clarification: bool
    clarifying_question: str
    context_envelope: dict[str, Any]
    context_blocks: list[dict[str, Any]]
    context_recent_messages: list[dict[str, Any]]
    context_report: dict[str, Any]
    context_hash: str
    approval: dict[str, Any] | None
    pending_action: str
    executed_writes: Annotated[list[dict[str, Any]], operator.add]
    guard_error: str | None
    final_answer: str
    status: Literal[
        "running",
        "waiting_approval",
        "resuming",
        "completed",
        "failed",
        "cancelled",
    ]
