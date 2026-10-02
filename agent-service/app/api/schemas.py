from typing import Any, Literal

from pydantic import BaseModel, Field

from app.context.models import ContextEnvelope


class UserContext(BaseModel):
    userId: str
    email: str | None = None
    name: str | None = None
    department: str | None = None
    roles: list[str] = Field(default_factory=list)
    managerId: str | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class AgentStatePayload(BaseModel):
    requestId: str
    userContext: UserContext
    intent: str | None = None
    slots: dict[str, str] = Field(default_factory=dict)
    missingFields: list[str] = Field(default_factory=list)
    riskLevel: Literal["low", "medium", "high"] | None = None
    plan: list[str] = Field(default_factory=list)
    retrievedDocuments: list[Any] = Field(default_factory=list)
    toolResults: list[Any] = Field(default_factory=list)
    approval: Any = None
    status: Literal["processing", "completed", "failed", "cancelled"] = (
        "processing"
    )


class ChatRequest(BaseModel):
    request_id: str
    user_context: UserContext
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    latest_user_message: str
    model: str | None = None
    api_key: str | None = None
    enable_thinking: bool = False
    enable_web_search: bool = False
    context_envelope: ContextEnvelope | None = None
    agent_state: AgentStatePayload
