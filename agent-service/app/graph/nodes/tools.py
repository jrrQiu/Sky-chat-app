"""The tool-execution node: choose tools, authorize them, run them, report what happened.

Tool choice now comes from the model when it can (function calling) and from rules when it
cannot. Whichever way a call arrives, the same three gates are applied afterwards -- because a
model's choice is a *request*, not an authorization:

1. the agent must be allowed to use the tool (`agents.yaml`);
2. the user must hold the permission (`registry/rbac.py`, itself derived from the catalog);
3. the arguments must match the tool's declared schema (`core/guardrails.py`).

Approval-gated tools are never offered to the model and are refused here if they somehow appear,
so this node cannot become a way around the approval gate.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.core.guardrails import enforce_tool_rails
from app.registry.rbac import authorize_tool
from app.registry.registry import can_agent_use_tool
from app.tools.adapters import get_tool_adapter
from app.tools.calling import FunctionCallingUnavailable, choose_tools, note_routing_mode
from app.tools.catalog import TOOLS, direct_tools
from app.tools.selection import select_tools_for_request

logger = logging.getLogger(__name__)


def _correlation_args(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": state.get("request_id"),
        "user_id": state.get("user_id"),
        "intent": state.get("intent"),
        "risk_level": state.get("risk_level"),
        "latest_user_message": state.get("latest_user_message"),
    }


def allowed_tools_for(state: dict[str, Any]) -> list[str]:
    """The tools this agent *and* this user may call, minus the approval-gated ones."""
    agent = str(state.get("selected_agent", "knowledge"))
    context = state.get("user_context")
    return [
        name
        for name in direct_tools()
        if can_agent_use_tool(agent, name) and authorize_tool(context, agent, name)
    ]


async def decide_tool_calls(
    state: dict[str, Any],
    *,
    poster: Any = None,
) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """Return the calls to make plus the mode that produced them ("model" or "rule-based")."""
    message = str(state.get("latest_user_message", ""))
    candidates = allowed_tools_for(state)

    if candidates:
        try:
            choices = await choose_tools(message, candidates, poster=poster)
        except FunctionCallingUnavailable as error:
            logger.info("Falling back to rule-based tool selection: %s", error)
            choices = []
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("Tool calling failed (%s); using rules", error)
            choices = []

        if choices:
            return [(choice.name, choice.arguments) for choice in choices], "model"

    fallback = select_tools_for_request(
        str(state.get("selected_agent", "knowledge")),
        message,
        str(state.get("action", "query")),
    )
    return [(name, {}) for name in fallback], "rule-based"


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

    calls, mode = await decide_tool_calls(state)
    # Record what actually chose, so `/ready` can distinguish "the model chose" from "the model
    # was unavailable and the rules chose".
    note_routing_mode(mode)

    new_tool_results: list[dict[str, Any]] = []
    retrieved_documents = list(state.get("retrieved_documents", []))
    approval = state.get("approval")
    tool_adapter = get_tool_adapter()

    async def refuse(tool_name: str, text: str, call_id: str | None = None) -> None:
        new_tool_results.append({"toolName": tool_name, "success": False, "text": text})
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id or f"tool_{uuid.uuid4().hex[:12]}",
                    "name": tool_name,
                    "success": False,
                    "message": text,
                }
            )

    for tool_name, model_args in calls:
        if tool_name not in TOOLS or TOOLS[tool_name].approval_gated:
            # An approval-gated tool must arrive through the approval gate, never here.
            await refuse(tool_name, f"工具 {tool_name} 需要审批，不能在对话中直接执行")
            continue

        if not can_agent_use_tool(selected_agent, tool_name):
            await refuse(tool_name, f"Tool {tool_name} is not allowed for {selected_agent}")
            continue

        if not authorize_tool(state.get("user_context"), selected_agent, tool_name):
            await refuse(tool_name, f"RBAC denied tool {tool_name} for current user roles")
            continue

        tool_call_id = f"tool_{uuid.uuid4().hex[:12]}"
        # The model's arguments plus the correlation values it is not allowed to invent. The
        # guardrail then rejects anything else, so a model cannot smuggle in extra arguments.
        args = {**_correlation_args(state), **model_args}

        # Execution rail (before): the model does not get to invent arguments.
        refusal = enforce_tool_rails(tool_name, args, {})
        if refusal is not None:
            await refuse(tool_name, str(refusal.get("text", "工具调用被安全护栏拒绝")), tool_call_id)
            continue

        if emit:
            await emit(
                {
                    "type": "tool_call",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "args": args,
                    "chosenBy": mode,
                }
            )

        tool_result = await tool_adapter.execute(tool_name, args, state)

        # Execution rail (after): tool output re-enters the model loop.
        refusal = enforce_tool_rails(tool_name, args, tool_result)
        if refusal is not None:
            await refuse(tool_name, str(refusal.get("text", "工具返回内容被安全护栏拒绝")), tool_call_id)
            continue

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
        "tool_routing": mode,
    }
