import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.registry.rbac import authorize_tool
from app.registry.registry import can_agent_use_tool
from app.tools.adapters import get_tool_adapter, select_tools_for_request


async def execute_tools(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = config.get("configurable", {}).get("emit") if config else None
    selected_agent = str(state.get("selected_agent", "knowledge"))
    if state.get("action") == "capabilities":
        return {
            "tool_results": [],
            "knowledge_documents": [],
            "retrieved_documents": list(state.get("retrieved_documents", [])),
            "approval": state.get("approval"),
        }

    tool_names = select_tools_for_request(
        selected_agent,
        str(state.get("latest_user_message", "")),
        str(state.get("action", "query")),
    )

    new_tool_results: list[dict[str, Any]] = []
    retrieved_documents = list(state.get("retrieved_documents", []))
    approval = state.get("approval")
    tool_adapter = get_tool_adapter()

    for tool_name in tool_names:
        if not can_agent_use_tool(selected_agent, tool_name):
            result = {
                "toolName": tool_name,
                "success": False,
                "text": f"Tool {tool_name} is not allowed for {selected_agent}",
            }
            if emit:
                await emit(
                    {
                        "type": "tool_result",
                        "toolCallId": f"tool_{uuid.uuid4().hex[:12]}",
                        "name": tool_name,
                        "success": False,
                        "message": result["text"],
                    }
                )
            new_tool_results.append(result)
            continue

        if not authorize_tool(state.get("user_context"), selected_agent, tool_name):
            result = {
                "toolName": tool_name,
                "success": False,
                "text": f"RBAC denied tool {tool_name} for current user roles",
            }
            if emit:
                await emit(
                    {
                        "type": "tool_result",
                        "toolCallId": f"tool_{uuid.uuid4().hex[:12]}",
                        "name": tool_name,
                        "success": False,
                        "message": result["text"],
                    }
                )
            new_tool_results.append(result)
            continue

        tool_call_id = f"tool_{uuid.uuid4().hex[:12]}"
        args = {
            "request_id": state.get("request_id"),
            "user_id": state.get("user_id"),
            "intent": state.get("intent"),
            "risk_level": state.get("risk_level"),
            "latest_user_message": state.get("latest_user_message"),
        }

        if emit:
            await emit(
                {
                    "type": "tool_call",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "args": args,
                }
            )

        tool_result = await tool_adapter.execute(tool_name, args, state)
        tool_result = {**tool_result, "toolName": tool_name}
        new_tool_results.append(tool_result)

        if tool_result.get("approval"):
            approval = tool_result["approval"]

        if tool_result.get("citations"):
            retrieved_documents.extend(
                {"id": citation} for citation in tool_result["citations"]
            )

        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "success": tool_result.get("success", False),
                    "message": tool_result.get("text", ""),
                    "resultCount": len(tool_result.get("citations", [])),
                    "sources": [
                        {"title": citation, "url": ""}
                        for citation in tool_result.get("citations", [])
                    ],
                }
            )

    return {
        "tool_results": new_tool_results,
        "knowledge_documents": [],
        "retrieved_documents": retrieved_documents,
        "approval": approval,
    }
