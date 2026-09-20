from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    request_id: str
    user_id: str
    user_context: dict[str, Any]
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    messages: list[dict[str, str]]
    latest_user_message: str
    model: str | None
    api_key: str
    enable_thinking: bool
    enable_web_search: bool
    intent: str | None
    action: str
    selected_agent: str
    risk_level: str | None
    plan: list[str]
    missing_fields: list[str]
    retrieved_documents: list[Any]
    knowledge_context: str
    tool_results: list[Any]
    approval: Any
    status: str
    guard_error: str | None
    final_answer: str
