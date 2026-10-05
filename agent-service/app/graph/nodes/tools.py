"""The agent loop: choose tools, run them, feed the results back, choose again.

Before this the model chose once, the tools ran, and the answer was written from the results.
That cannot express "look up the ticket, then comment on it", because the second step depends on
what the first returned. Now the model sees each result and decides whether it needs another
call, up to a bounded number of rounds.

Everything that guarded a single choice still guards every round, because a loop makes each of
them matter more rather than less:

* the agent allow-list and the user's permission are re-checked per call;
* the argument schema is enforced per call;
* **an exact duplicate call is refused**, so a model that repeats itself cannot create the same
  ticket twice -- the failure mode a loop introduces that a single shot did not have;
* rounds and total calls are capped, so a confused model is a bounded cost, not an open one;
* approval-gated tools are never offered and are refused if they appear.

When no model is available the loop degenerates to the previous single rule-based round, and the
mode is reported as ``rule-based`` rather than dressed up as model-driven.
"""

import json
import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.core.guardrails import enforce_tool_rails
from app.registry.rbac import authorize_tool
from app.registry.registry import can_agent_use_tool
from app.tools.adapters import get_tool_adapter
from app.tools.calling import (
    FunctionCallingUnavailable,
    ModelTurn,
    converse,
    note_routing_mode,
    tool_result_message,
)
from app.tools.calling import _messages as build_messages
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


def _fingerprint(name: str, arguments: dict[str, Any]) -> str:
    """A stable identity for a call, used to refuse exact repeats."""
    return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)}"


async def run_tool_loop(
    state: dict[str, Any],
    *,
    emit: Any = None,
    poster: Any = None,
) -> tuple[list[dict[str, Any]], str, int]:
    """Run the tool loop. Returns ``(results, mode, rounds)``."""
    message = str(state.get("latest_user_message", ""))
    candidates = allowed_tools_for(state)
    adapter = get_tool_adapter()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    mode = "rule-based"
    rounds = 0

    async def report(tool_name: str, text: str, call_id: str | None = None,
                     round_index: int = 0) -> dict[str, Any]:
        result = {"toolName": tool_name, "success": False, "text": text}
        results.append(result)
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id or f"tool_{uuid.uuid4().hex[:12]}",
                    "name": tool_name,
                    "success": False,
                    "message": text,
                    "round": round_index,
                }
            )
        return result

    async def run_one(
        tool_name: str,
        model_args: dict[str, Any],
        call_id: str,
        round_index: int,
    ) -> dict[str, Any]:
        if tool_name not in TOOLS or TOOLS[tool_name].approval_gated:
            return await report(
                tool_name,
                f"工具 {tool_name} 需要审批，不能在对话中直接执行",
                call_id,
                round_index,
            )
        if not can_agent_use_tool(str(state.get("selected_agent", "knowledge")), tool_name):
            return await report(tool_name, f"Tool {tool_name} is not allowed for this agent",
                                call_id, round_index)
        if not authorize_tool(state.get("user_context"), str(state.get("selected_agent")),
                              tool_name):
            return await report(tool_name, f"RBAC denied tool {tool_name} for current user roles",
                                call_id, round_index)

        fingerprint = _fingerprint(tool_name, model_args)
        if fingerprint in seen:
            # A loop can repeat itself. With real writes that means a second ticket, so an exact
            # repeat is refused instead of executed.
            return await report(
                tool_name,
                f"重复调用已忽略：{tool_name} 用相同参数已经执行过一次。",
                call_id,
                round_index,
            )
        seen.add(fingerprint)

        args = {**_correlation_args(state), **model_args}
        refusal = enforce_tool_rails(tool_name, args, {})
        if refusal is not None:
            return await report(tool_name, str(refusal.get("text", "工具调用被安全护栏拒绝")),
                                call_id, round_index)

        if emit:
            await emit(
                {
                    "type": "tool_call",
                    "toolCallId": call_id,
                    "name": tool_name,
                    "args": args,
                    "chosenBy": mode,
                    "round": round_index,
                }
            )

        tool_result = await adapter.execute(tool_name, args, state)

        refusal = enforce_tool_rails(tool_name, args, tool_result)
        if refusal is not None:
            return await report(tool_name, str(refusal.get("text", "工具返回内容被安全护栏拒绝")),
                                call_id, round_index)

        tool_result = {**tool_result, "toolName": tool_name}
        results.append(tool_result)
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id,
                    "name": tool_name,
                    "success": tool_result.get("success", False),
                    "message": tool_result.get("text", ""),
                    "round": round_index,
                }
            )
        return tool_result

    messages = build_messages(message, None)
    budget = max(1, settings.agent_max_tool_calls)

    # No candidates means no point asking: `converse` would be handed an empty tool list and the
    # model could only answer, which is not a tool decision.
    while candidates and rounds < max(1, settings.agent_max_rounds):
        try:
            turn: ModelTurn = await converse(messages, candidates, poster=poster)
        except FunctionCallingUnavailable as error:
            logger.info("No model for tool choice; using rules: %s", error)
            break
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("Tool calling failed (%s); using rules", error)
            break

        if not turn.calls:
            # The model is ready to answer. Recording the mode matters: it means the fallback did
            # not run, so `/ready` should not report `fellBack`. This turn chose no tools, so it
            # is not counted as a round -- the cap governs rounds that actually run tools.
            mode = "model"
            break

        mode = "model"
        rounds += 1
        messages.append(turn.assistant_message or {"role": "assistant"})
        for choice in turn.calls:
            if budget <= 0:
                break
            budget -= 1
            outcome = await run_one(choice.name, choice.arguments, choice.call_id, rounds)
            messages.append(tool_result_message(choice.call_id, outcome))

        if budget <= 0:
            if emit:
                await emit({
                    "type": "thinking",
                    "content": f"已达到本轮工具调用上限（{settings.agent_max_tool_calls} 次），停止继续调用。",
                    "step": True,
                })
            break

    if mode == "rule-based":
        rounds = 1
        fallback = (
            select_tools_for_request(
                str(state.get("selected_agent", "knowledge")),
                message,
                str(state.get("action", "query")),
            )
            if candidates
            else []
        )
        for name in fallback:
            if budget <= 0:
                break
            budget -= 1
            await run_one(name, {}, f"tool_{uuid.uuid4().hex[:12]}", rounds)

    note_routing_mode(mode)
    return results, mode, rounds


async def execute_tools(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = config.get("configurable", {}).get("emit") if config else None
    if state.get("action") == "capabilities":
        return {
            "tool_results": [],
            "knowledge_documents": [],
            "retrieved_documents": list(state.get("retrieved_documents", [])),
            "approval": state.get("approval"),
        }

    results, mode, rounds = await run_tool_loop(state, emit=emit)

    approval = state.get("approval")
    retrieved_documents = list(state.get("retrieved_documents", []))
    for result in results:
        if result.get("approval"):
            approval = result["approval"]
        for citation in result.get("citations", []) or []:
            retrieved_documents.append({"id": citation})

    return {
        "tool_results": results,
        "knowledge_documents": [],
        "retrieved_documents": retrieved_documents,
        "approval": approval,
        "tool_routing": mode,
        "tool_rounds": rounds,
    }
