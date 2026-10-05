"""The agent loop: choose tools, run them, feed the results back, choose again.

Before this the model chose once, the tools ran, and the answer was written from the results.
That cannot express "look up the ticket, then act on it", because the second step depends on what
the first returned. Now the model sees each result and decides whether it needs another call.

A loop makes every guard matter more, not less, so each one is applied per call and per round:

* the agent allow-list, the user's permission and the argument schema;
* **an exact duplicate call is refused** -- with real writes, a model repeating itself would
  otherwise raise a second ticket;
* **every state-changing call goes through the effect ledger** keyed by turn + tool + arguments,
  so a node replay (a crash mid-loop, a re-delivered turn) cannot apply the same write twice.
  This is the honest answer to "the loop is not checkpointed per round": the *trace* is in state
  after the node, and the *effects* are idempotent, so a replay is safe even though resume
  granularity is per node;
* rounds, total calls and tokens are capped, so a confused model is a bounded cost;
* read-only calls in one round run **concurrently**; state-changing ones run in the order the
  model asked for them, because a sequence of writes is the part where order is meaning.

When no model is available the loop degenerates to the previous single rule-based round, and the
mode is reported as ``rule-based`` rather than dressed up as model-driven.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.core.effect_ledger import effect_key_for, effect_ledger
from app.core.guardrails import enforce_tool_rails
from app.registry.rbac import authorize_tool
from app.registry.registry import can_agent_use_tool
from app.tools.adapters import get_tool_adapter
from app.tools.calling import (
    FunctionCallingUnavailable,
    ModelTurn,
    converse,
    converse_stream,
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


async def model_turn(
    messages: list[dict[str, Any]],
    candidates: list[str],
    *,
    emit: Any = None,
    streamer: Any = None,
    poster: Any = None,
) -> ModelTurn:
    """One assistant turn, streaming when there is somewhere to send the reasoning.

    Kept as a single seam so the loop has exactly one place where a model is called, and so tests
    do not have to know whether a given turn streamed.
    """
    delta_sink = None
    if emit and settings.agent_stream_decisions:
        async def delta_sink(piece: str) -> None:  # type: ignore[misc]
            # Forwarded as `thinking` so the user watches the decision being made instead of
            # staring at a generic line while a non-streaming call completes.
            await emit({"type": "thinking", "content": piece})

    if streamer is not None or delta_sink is not None:
        return await converse_stream(
            messages, candidates, on_delta=delta_sink, streamer=streamer
        )
    return await converse(messages, candidates, poster=poster)


async def run_tool_loop(
    state: dict[str, Any],
    *,
    emit: Any = None,
    streamer: Any = None,
    poster: Any = None,
) -> tuple[list[dict[str, Any]], str, int, dict[str, Any]]:
    """Run the tool loop.

    Returns ``(results, mode, rounds, accounting)``; ``accounting`` carries the token usage and,
    when the loop stopped early, the reason.
    """
    message = str(state.get("latest_user_message", ""))
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    candidates = allowed_tools_for(state)
    adapter = get_tool_adapter()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    mode = "rule-based"
    rounds = 0
    tokens = 0
    stop_reason: str | None = None

    async def report(
        tool_name: str, text: str, call_id: str | None = None, round_index: int = 0
    ) -> dict[str, Any]:
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

    async def guarded(
        tool_name: str,
        model_args: dict[str, Any],
        call_id: str,
        round_index: int,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Apply every gate. Returns ``(args, refusal)``; exactly one is set."""
        if tool_name not in TOOLS or TOOLS[tool_name].approval_gated:
            return None, await report(
                tool_name, f"工具 {tool_name} 需要审批，不能在对话中直接执行", call_id, round_index
            )
        if not can_agent_use_tool(str(state.get("selected_agent", "knowledge")), tool_name):
            return None, await report(
                tool_name, f"Tool {tool_name} is not allowed for this agent", call_id, round_index
            )
        if not authorize_tool(
            state.get("user_context"), str(state.get("selected_agent")), tool_name
        ):
            return None, await report(
                tool_name, f"RBAC denied tool {tool_name} for current user roles", call_id, round_index
            )

        fingerprint = _fingerprint(tool_name, model_args)
        if fingerprint in seen:
            # A loop can repeat itself. With real writes that means a second ticket, so an exact
            # repeat is refused instead of executed.
            return None, await report(
                tool_name,
                f"重复调用已忽略：{tool_name} 用相同参数已经执行过一次。",
                call_id,
                round_index,
            )
        # Claimed before any await, so two identical calls in the same concurrent batch cannot
        # both pass this check.
        seen.add(fingerprint)

        args = {**_correlation_args(state), **model_args}
        refusal = enforce_tool_rails(tool_name, args, {})
        if refusal is not None:
            return None, await report(
                tool_name, str(refusal.get("text", "工具调用被安全护栏拒绝")), call_id, round_index
            )
        return args, None

    async def invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call the tool, wrapping state-changing calls in the effect ledger.

        The ledger is what makes a replay safe: a crash mid-loop re-runs this node from the start,
        and without it every write in the loop would be applied a second time.
        """
        if not TOOLS[tool_name].writes:
            return await adapter.execute(tool_name, args, state)

        # The ledger's `action` column is varchar(64), so the per-call distinction is a short
        # digest rather than the arguments themselves -- the first version embedded them and the
        # insert failed, which the ledger reported by refusing to run the write at all.
        digest = hashlib.sha256(_fingerprint(tool_name, args).encode("utf-8")).hexdigest()[:16]
        action = f"{tool_name}#{digest}"[:64]
        return await effect_ledger.run_once(
            turn_id=turn_id,
            action=action,
            approval_id=None,
            execute=lambda _provider_key: adapter.execute(tool_name, args, state),
        )

    async def run_one(
        tool_name: str,
        model_args: dict[str, Any],
        call_id: str,
        round_index: int,
    ) -> dict[str, Any]:
        args, refusal = await guarded(tool_name, model_args, call_id, round_index)
        if refusal is not None:
            return refusal

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

        try:
            tool_result = await invoke(tool_name, args)
        except Exception as error:
            # A tool that raises is a failed call, not a failed turn: the model may be able to
            # choose a different route with the failure in hand.
            logger.warning("Tool %s raised: %s", tool_name, error)
            return await report(tool_name, f"工具执行异常：{error}", call_id, round_index)

        refusal = enforce_tool_rails(tool_name, args, tool_result)
        if refusal is not None:
            return await report(
                tool_name, str(refusal.get("text", "工具返回内容被安全护栏拒绝")), call_id, round_index
            )

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

    async def run_round(calls: list[Any], round_index: int) -> list[dict[str, Any]]:
        """Read-only calls together; writes in the order the model asked for them."""
        reads = [c for c in calls if not TOOLS[c.name].writes] if all(
            c.name in TOOLS for c in calls
        ) else []
        writes = [c for c in calls if c not in reads]

        outcomes: list[dict[str, Any]] = []
        if reads:
            outcomes.extend(
                await asyncio.gather(
                    *(run_one(c.name, c.arguments, c.call_id, round_index) for c in reads)
                )
            )
        for call in writes:
            outcomes.append(await run_one(call.name, call.arguments, call.call_id, round_index))
        return outcomes

    messages = build_messages(message, None)
    budget = max(1, settings.agent_max_tool_calls)

    # No candidates means no point asking: the model would be handed an empty tool list and could
    # only answer, which is not a tool decision.
    while candidates and rounds < max(1, settings.agent_max_rounds):
        try:
            turn: ModelTurn = await model_turn(
                messages, candidates, emit=emit, streamer=streamer, poster=poster
            )
        except FunctionCallingUnavailable as error:
            logger.info("No model for tool choice; using rules: %s", error)
            break
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("Tool calling failed (%s); using rules", error)
            break

        if turn.usage:
            tokens += turn.usage.get("totalTokens", 0)
        if settings.agent_token_budget and tokens > settings.agent_token_budget:
            stop_reason = "TOKEN_BUDGET"
            if emit:
                await emit({
                    "type": "thinking",
                    "content": f"已达到 token 预算（{tokens}/{settings.agent_token_budget}），停止继续调用工具。",
                    "step": True,
                })
            break

        if not turn.calls:
            # The model is ready to answer. Recording the mode matters: it means the fallback did
            # not run, so `/ready` should not report `fellBack`. This turn chose no tools, so it is
            # not counted as a round -- the cap governs rounds that actually run tools.
            mode = "model"
            break

        mode = "model"
        rounds += 1
        messages.append(turn.assistant_message or {"role": "assistant"})
        batch = turn.calls[:budget] if budget < len(turn.calls) else turn.calls
        budget -= len(batch)
        for call, outcome in zip(batch, await run_round(batch, rounds)):
            messages.append(tool_result_message(call.call_id, outcome))

        if budget <= 0:
            stop_reason = "TOOL_CALL_BUDGET"
            if emit:
                await emit({
                    "type": "thinking",
                    "content": f"已达到本轮工具调用上限（{settings.agent_max_tool_calls} 次），停止继续调用。",
                    "step": True,
                })
            break

        if settings.agent_stop_after_write and any(
            outcome.get("success") and outcome.get("toolName") in TOOLS
            and TOOLS[outcome["toolName"]].writes
            for outcome in results
        ):
            # A successful write *is* the outcome. Another round could only add duplicate writes or
            # reads the answer can already be written from.
            stop_reason = "WRITE_SUCCEEDED"
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
    return results, mode, rounds, {
        "tokens": tokens or None,
        "stopReason": stop_reason,
        "modelTurns": rounds if mode == "model" else 0,
    }


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

    results, mode, rounds, accounting = await run_tool_loop(state, emit=emit)

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
        "tool_accounting": accounting,
    }
