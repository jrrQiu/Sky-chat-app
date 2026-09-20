import json
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig

from app.api.schemas import ChatRequest
from app.gateway.litellm import generate_title
from app.graph.builder import build_agent_graph


Emit = Callable[[dict[str, Any]], Awaitable[None]]


def initial_state(request: ChatRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "user_id": request.user_context.userId,
        "user_context": {
            "userId": request.user_context.userId,
            "email": request.user_context.email,
            "name": request.user_context.name,
            "department": request.user_context.department,
            "roles": request.user_context.roles,
            "managerId": request.user_context.managerId,
        },
        "conversation_id": request.conversation_id,
        "user_message_id": request.user_message_id,
        "assistant_message_id": request.assistant_message_id,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "latest_user_message": request.latest_user_message,
        "model": request.model,
        "api_key": request.api_key or "",
        "enable_thinking": request.enable_thinking,
        "enable_web_search": request.enable_web_search,
        "intent": request.agent_state.intent,
        "action": request.agent_state.slots.get("action", "query"),
        "selected_agent": request.agent_state.slots.get(
            "selected_agent", "knowledge"
        ),
        "risk_level": request.agent_state.riskLevel,
        "plan": request.agent_state.plan,
        "missing_fields": request.agent_state.missingFields,
        "retrieved_documents": list(request.agent_state.retrievedDocuments),
        "knowledge_context": "",
        "tool_results": list(request.agent_state.toolResults),
        "approval": request.agent_state.approval,
        "status": "processing",
        "guard_error": None,
        "final_answer": "",
    }


async def run_graph(request: ChatRequest, emit: Emit) -> None:
    graph = build_agent_graph()
    config: RunnableConfig = {"configurable": {"emit": emit}}
    async for _ in graph.astream(
        initial_state(request),
        config=config,
        stream_mode="values",
    ):
        pass

    if len(request.messages) == 1:
        title = await generate_title(
            request.latest_user_message,
            api_key=request.api_key,
        )
        if title != "新对话":
            await emit({"type": "conversation_title", "content": title})

    await emit({"type": "complete"})


def format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return f"event: message\ndata: {data}\n\n"
