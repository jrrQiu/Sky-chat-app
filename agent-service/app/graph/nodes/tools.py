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
import time
import uuid
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.config import settings
from app.core.audit import record_action
from app.core.effect_ledger import effect_ledger
from app.core.guardrails import enforce_tool_rails
from app.observability import tool_metrics
from app.registry.rbac import authorize_tool
from app.registry.registry import can_agent_use_tool
from app.tools.adapters import get_tool_adapter
from app.tools.adapters.contract import reset_idempotency_key, set_idempotency_key
from app.tools.calling import (
    FunctionCallingUnavailable,
    ModelTurn,
    converse,
    converse_stream,
    estimated_tokens,
    note_routing_mode,
    reset_model_override,
    set_model_override,
    tool_result_message,
)
from app.tools.calling import _messages as build_messages
from app.tools.catalog import (
    TOOLS,
    WRITE_CONFIRM,
    dig,
    direct_tools,
    result_contract,
    write_policy_for,
)
from app.tools import failures
from app.tools.resilience import default_registry
from app.tools.selection import select_tools_for_request

logger = logging.getLogger(__name__)

#: Returned when the local circuit breaker has decided not to touch the network.
TOOL_CIRCUIT_OPEN = "TOOL_CIRCUIT_OPEN"
#: A "success" that is missing a required field.
RESULT_SCHEMA_VIOLATION = "RESULT_SCHEMA_VIOLATION"
#: The per-call deadline fired. The call is over; whether the effect landed is not.
TOOL_CALL_TIMEOUT = "TOOL_CALL_TIMEOUT"
#: A write was interrupted (deadline or cancellation) and its outcome cannot be confirmed.
RESULT_UNKNOWN = "RESULT_UNKNOWN"


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


def _is_transport(error: BaseException) -> bool:
    """Transport failure as opposed to a business failure.

    Only transport failures count towards a circuit breaker: an HTTP 4xx means the
    dependency answered, so it is healthy and the fault is ours. The Java
    ``AgentCircuitBreaker`` draws the line in the same place.
    """
    return isinstance(error, httpx.HTTPError)


def apply_result_contract(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Enforce the tool's declared result contract (§4.5).

    A result that claims success while missing a declared dot-path is rewritten into a
    failure. The point is not tidiness: ``ticket_create`` returning ``success`` without a
    number is precisely the input that lets the model fill the gap with a plausible
    ticket number, which this project has otherwise refused to do everywhere.
    """
    required, _allowed = result_contract(tool_name)
    if not required or not result.get("success"):
        return result
    missing = [path for path in required if dig(result, path) is None]
    if not missing:
        return result
    logger.warning(
        "Tool %s reported success without %s; rewriting to a schema violation",
        tool_name,
        missing,
    )
    return {
        **result,
        "success": False,
        "error": RESULT_SCHEMA_VIOLATION,
        "text": (
            f"工具 {tool_name} 返回了成功但缺少必需字段（{', '.join(missing)}），"
            "本次结果不可信，请勿据此推断编号或结论。"
        ),
        "missingFields": missing,
    }


def _record_breaker(registry, tool_name: str, result: dict[str, Any]) -> None:
    """Feed the outcome to the breaker, using the one classification table.

    Only a failure of the dependency itself counts. Everything else -- our own refusals, the
    dependency answering correctly and us being wrong -- records a success, which also clears a
    stale failure count so an unrelated refusal cannot leave the breaker primed.
    """
    if result.get("success"):
        registry.breaker(tool_name).record_success()
        return
    error = str(result.get("error") or "")
    if failures.counts_towards_breaker(error):
        registry.breaker(tool_name).record_failure(error, transport=True)
    else:
        registry.breaker(tool_name).record_success()


def _record_metrics(tool_name: str, result: dict[str, Any], latency_ms: float) -> None:
    tool_metrics.record_tool_call(
        tool_name,
        outcome=tool_metrics.outcome_for(result),
        latency_ms=latency_ms,
    )


def _advice_recovery(outcome: dict[str, Any]) -> str:
    """The recovery mode the failure contract declared for one tool outcome.

    Reads the code through ``advice_for`` rather than the outcome dict, so the
    decision stays in one table instead of being re-derived at the call site.
    """
    error = outcome.get("error")
    if not error:
        return ""
    try:
        from app.tools.failures import advice_for

        return advice_for(str(error)).recovery
    except Exception:  # pragma: no cover - advice must never break the loop
        return ""


def _account_tokens(
    turn: ModelTurn,
    messages: list[dict[str, Any]],
    running_total: int,
) -> tuple[int, str]:
    """Advance the loop's token count, never treating "unreported" as zero.

    When the provider reports usage, only the *new* input plus the output is added --
    the prompt already includes the previous rounds, so summing ``total_tokens`` across
    rounds counts the same tokens repeatedly and trips the budget early. When it reports
    nothing, the same tokenizer the context engine uses produces an estimate and the
    caller is told the number is estimated (T6).
    """
    usage = turn.usage or {}
    reported = int(usage.get("promptTokens") or 0) + int(usage.get("completionTokens") or 0)
    if reported > 0:
        return running_total + reported, tool_metrics.USAGE_REPORTED
    estimated = estimated_tokens(messages)
    return running_total + estimated, tool_metrics.USAGE_ESTIMATED


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
    delta_sink: Any = None
    if emit and settings.agent_stream_decisions:

        async def forward_decision(piece: str) -> None:
            # Forwarded as `thinking` so the user watches the decision being made instead of
            # staring at a generic line while a non-streaming call completes.
            await emit({"type": "thinking", "content": piece})

        delta_sink = forward_decision

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
    runtime: Runtime | None = None,
) -> tuple[list[dict[str, Any]], str, int, dict[str, Any]]:
    """Run the tool loop.

    Returns ``(results, mode, rounds, accounting)``; ``accounting`` carries the token usage
    and, when the loop stopped early, the reason. ``pendingConfirmWrites`` inside it is what
    the graph's confirmation node consumes.
    """
    message = str(state.get("latest_user_message", ""))
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    candidates = allowed_tools_for(state)
    adapter = get_tool_adapter()
    breakers = default_registry()
    results: list[dict[str, Any]] = []
    pending_confirm: list[dict[str, Any]] = []
    seen: set[str] = set()
    mode = "rule-based"
    rounds = 0
    tokens = 0
    usage_source = tool_metrics.USAGE_REPORTED
    stop_reason: str | None = None
    loop_started = time.perf_counter()

    async def heartbeat(tool_name: str, call_id: str, note: str) -> None:
        """Prove the turn is still moving.

        Two audiences, one signal: the client sees a ``tool_progress`` event instead of a
        frozen "thinking" line, and the framework's idle timer is refreshed so a long but
        healthy tool is not killed as a hung node.
        """
        if runtime is not None:
            try:
                runtime.heartbeat()
            except Exception:  # pragma: no cover - depends on the runner
                logger.debug("runtime.heartbeat() is unavailable in this context")
        if not emit:
            return
        await emit(
            {
                "type": "tool_progress",
                "toolCallId": call_id,
                "name": tool_name,
                "progress": None,
                "note": note,
            }
        )

    async def tick() -> None:
        """Refresh the node's idle timer without emitting anything to the client.

        The model call between rounds can take up to `llm_timeout_seconds`, so the node needs
        a heartbeat that is not tied to a tool's progress event.
        """
        if runtime is None:
            return
        try:
            runtime.heartbeat()
        except Exception:  # pragma: no cover - depends on the runner
            logger.debug("runtime.heartbeat() is unavailable in this context")

    async def with_heartbeat(tool_name: str, call_id: str, awaitable):
        """Await ``awaitable`` while emitting a heartbeat every N seconds."""
        interval = settings.tool_heartbeat_seconds
        if not emit or interval <= 0:
            return await awaitable
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if done:
                    return task.result()
                await heartbeat(tool_name, call_id, "still running")
        finally:
            if not task.done():
                # The consumer went away: cancel rather than leave the tool running with
                # nobody to deliver its result to.
                task.cancel()

    async def report(
        tool_name: str,
        text: str,
        call_id: str | None = None,
        round_index: int = 0,
        *,
        error: str | None = None,
        outcome: str | None = None,
        latency_ms: float = 0.0,
    ) -> dict[str, Any]:
        result = {"toolName": tool_name, "success": False, "text": text}
        if error:
            result["error"] = error
        results.append(result)
        tool_metrics.record_tool_call(
            tool_name,
            outcome=outcome or tool_metrics.outcome_for(result),
            latency_ms=latency_ms,
        )
        if error in {"GUARDRAIL_REFUSED", "RBAC_DENIED"}:
            # The same class of security event the approval path already audits. Without this
            # the loop's refusals lived only in metrics and logs, so "who tried what and was
            # refused" was unanswerable for the conversational path.
            await record_action(
                "guardrail.tool_refused" if error == "GUARDRAIL_REFUSED" else "tool.denied",
                object_type="tool",
                object_id=tool_name,
                outcome="denied",
                actor_id=str(state.get("user_id") or "") or None,
                detail={"turn_id": turn_id, "error": error, "round": round_index},
            )
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id or f"tool_{uuid.uuid4().hex[:12]}",
                    "name": tool_name,
                    "success": False,
                    "message": text,
                    "round": round_index,
                    "errorCode": error,
                    "latencyMs": round(latency_ms, 3),
                }
            )
        return result

    async def announce_unknown_outcome(
        tool_name: str,
        call_id: str,
        round_index: int,
        *,
        reason: str,
    ) -> None:
        """Record that a write's outcome cannot be confirmed, even while cancelling.

        The emit is shielded on purpose: this runs inside a cancellation path, where an
        unshielded await would be cancelled before the client ever saw it. Losing the event is
        still survivable -- the ledger row is the durable record -- so a failure here only logs.
        """
        result = {
            "toolName": tool_name,
            "success": False,
            "error": RESULT_UNKNOWN,
            "text": (
                f"「{tool_name}」执行被中断（{reason}），结果未知。"
                "请勿重复提交，稍后查询确认。"
            ),
        }
        results.append(result)
        tool_metrics.record_tool_call(
            tool_name, outcome=tool_metrics.OUTCOME_UNAVAILABLE, latency_ms=0.0
        )
        if not emit:
            return
        event = {
            "type": "tool_result",
            "toolCallId": call_id,
            "name": tool_name,
            "success": False,
            "message": result["text"],
            "round": round_index,
            "errorCode": RESULT_UNKNOWN,
        }
        try:
            await asyncio.shield(emit(event))
        except asyncio.CancelledError:
            logger.warning(
                "RESULT_UNKNOWN for %s (%s) cancelled while being delivered", call_id, reason
            )
        except Exception:
            logger.exception("Failed to emit RESULT_UNKNOWN for %s", call_id)

    async def guarded(
        tool_name: str,
        model_args: dict[str, Any],
        call_id: str,
        round_index: int,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Apply every gate. Returns ``(args, refusal)``; exactly one is set."""
        if tool_name not in TOOLS:
            # A model can name anything. This used to share the approval-gated message, which
            # told the operator "needs approval" about a tool that does not exist.
            return None, await report(
                tool_name,
                f"工具 {tool_name} 不在工具目录中，无法调用。",
                call_id,
                round_index,
                error="TOOL_UNKNOWN",
                outcome=tool_metrics.OUTCOME_FAILED,
            )
        if TOOLS[tool_name].approval_gated:
            return None, await report(
                tool_name,
                f"工具 {tool_name} 需要审批，不能在对话中直接执行",
                call_id,
                round_index,
                error="APPROVAL_GATED",
                outcome=tool_metrics.OUTCOME_REFUSED,
            )
        if not can_agent_use_tool(str(state.get("selected_agent", "knowledge")), tool_name):
            return None, await report(
                tool_name,
                f"Tool {tool_name} is not allowed for this agent",
                call_id,
                round_index,
                # Classified rather than left blank: without a code this refusal was counted as
                # a generic failure, which both misled the metrics and lost the "denied" signal.
                error="AGENT_TOOL_NOT_ALLOWED",
                outcome=tool_metrics.OUTCOME_DENIED,
            )
        if not authorize_tool(
            state.get("user_context"), str(state.get("selected_agent")), tool_name
        ):
            return None, await report(
                tool_name,
                f"RBAC denied tool {tool_name} for current user roles",
                call_id,
                round_index,
                error="RBAC_DENIED",
            )
        if breakers.breaker(tool_name).is_open():
            # The dependency has been unreachable repeatedly. Refusing locally is better
            # than spending the round's latency budget on a call that will fail.
            return None, await report(
                tool_name,
                f"工具 {tool_name} 的连接暂时不可用（连续失败已熔断），该操作未执行。",
                call_id,
                round_index,
                error=TOOL_CIRCUIT_OPEN,
                outcome=tool_metrics.OUTCOME_UNAVAILABLE,
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
                error="DUPLICATE_CALL_REFUSED",
                outcome=tool_metrics.OUTCOME_REFUSED,
            )
        # Claimed before any await, so two identical calls in the same concurrent batch cannot
        # both pass this check.
        seen.add(fingerprint)

        args = {**_correlation_args(state), **model_args}
        refusal = enforce_tool_rails(tool_name, args, {})
        if refusal is not None:
            return None, await report(
                tool_name,
                str(refusal.get("text", "工具调用被安全护栏拒绝")),
                call_id,
                round_index,
                error="GUARDRAIL_REFUSED",
                outcome=tool_metrics.OUTCOME_REFUSED,
            )
        return args, None

    async def execute_with_ledger(
        tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        digest = hashlib.sha256(_fingerprint(tool_name, args).encode("utf-8")).hexdigest()[:16]
        action = f"{tool_name}#{digest}"[:64]

        async def run(provider_key: str) -> dict[str, Any]:
            # The key is available *inside* the ledger, so it is bound here rather than
            # passed as a parameter -- an adapter cannot accidentally ignore it.
            token = set_idempotency_key(provider_key)
            try:
                return await adapter.execute(tool_name, args, state)
            finally:
                reset_idempotency_key(token)

        return await effect_ledger.run_once(
            turn_id=turn_id,
            action=action,
            approval_id=None,
            execute=run,
        )

    async def invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call the tool, wrapping state-changing calls in the effect ledger.

        The ledger is what makes a replay safe: a crash mid-loop re-runs this node from the start,
        and without it every write in the loop would be applied a second time.
        """
        if not TOOLS[tool_name].writes:
            return await adapter.execute(tool_name, args, state)
        return await execute_with_ledger(tool_name, args)

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

        # T7: a `confirm`-tier write does not execute here. It is recorded and handed to the
        # graph's confirmation node, so the user decides before anything leaves the process.
        # Deciding inside the loop would mean putting `interrupt()` here, which re-runs the
        # whole node -- and therefore the model calls -- on resume.
        if (
            settings.write_confirmation_enabled
            and TOOLS[tool_name].writes
            and write_policy_for(tool_name) == WRITE_CONFIRM
        ):
            pending_confirm.append(
                {
                    "callId": call_id,
                    "name": tool_name,
                    "args": args,
                    "round": round_index,
                }
            )
            return await report(
                tool_name,
                f"「{tool_name}」需要用户确认后才会执行，本次已暂停等待确认。",
                call_id,
                round_index,
                error="CONFIRMATION_REQUIRED",
                outcome=tool_metrics.OUTCOME_REFUSED,
            )

        started = time.perf_counter()
        writes = bool(TOOLS[tool_name].writes)
        try:
            tool_result = await with_heartbeat(
                tool_name,
                call_id,
                # A hard wall-clock cap on the whole call, retries included. httpx's own timeout
                # is per operation (and its read timeout measures the gap between reads), so
                # without this a dependency that drips bytes could hold the node for its entire
                # run budget while looking perfectly healthy to every other guard.
                asyncio.wait_for(
                    invoke(tool_name, args),
                    timeout=settings.tool_call_deadline_seconds,
                ),
            )
        except asyncio.CancelledError:
            # The node (or the client) went away while a write was in flight. Its outcome is
            # genuinely unknown, so say so before the cancellation unwinds: "no result" reads as
            # "nothing happened" to a user who may then resubmit, which is exactly what the
            # idempotency key exists to prevent.
            if writes:
                await announce_unknown_outcome(
                    tool_name, call_id, round_index, reason="cancelled"
                )
            raise
        except asyncio.TimeoutError:
            # Our own deadline, not a transport timeout: the call is over, the effect is not. A
            # dependency that times out is a failing dependency, so it counts towards the
            # breaker -- otherwise every round pays the full deadline again.
            latency_ms = (time.perf_counter() - started) * 1000.0
            breakers.breaker(tool_name).record_failure(TOOL_CALL_TIMEOUT, transport=True)
            if writes:
                return await report(
                    tool_name,
                    f"「{tool_name}」超过 {settings.tool_call_deadline_seconds:.0f}s 上限被中断，"
                    "执行结果未知。请勿重复提交，稍后查询确认。",
                    call_id,
                    round_index,
                    error=RESULT_UNKNOWN,
                    outcome=tool_metrics.OUTCOME_UNAVAILABLE,
                    latency_ms=latency_ms,
                )
            return await report(
                tool_name,
                f"工具 {tool_name} 调用超过 {settings.tool_call_deadline_seconds:.0f}s 上限已终止，"
                "该操作未执行。",
                call_id,
                round_index,
                error=TOOL_CALL_TIMEOUT,
                outcome=tool_metrics.OUTCOME_UNAVAILABLE,
                latency_ms=latency_ms,
            )
        except Exception as error:
            # A tool that raises is a failed call, not a failed turn: the model may be able to
            # choose a different route with the failure in hand.
            logger.warning("Tool %s raised: %s", tool_name, error)
            breakers.breaker(tool_name).record_failure(error, transport=_is_transport(error))
            return await report(
                tool_name,
                f"工具执行异常：{error}",
                call_id,
                round_index,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        latency_ms = (time.perf_counter() - started) * 1000.0
        refusal = enforce_tool_rails(tool_name, args, tool_result)
        if refusal is not None:
            return await report(
                tool_name,
                str(refusal.get("text", "工具返回内容被安全护栏拒绝")),
                call_id,
                round_index,
                error="GUARDRAIL_REFUSED",
                outcome=tool_metrics.OUTCOME_REFUSED,
                latency_ms=latency_ms,
            )

        tool_result = {**tool_result, "toolName": tool_name}
        # T8/§4.5: a "success" that is missing the identifiers the tool promised is not a
        # success. This is what stops the model from taking a success-shaped result with no
        # ticket number and inventing one.
        tool_result = apply_result_contract(tool_name, tool_result)
        results.append(tool_result)
        _record_breaker(breakers, tool_name, tool_result)
        _record_metrics(tool_name, tool_result, latency_ms)
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id,
                    "name": tool_name,
                    "success": tool_result.get("success", False),
                    "message": tool_result.get("text", ""),
                    "round": round_index,
                    "attempts": tool_result.get("attempts", 1),
                    "errorCode": tool_result.get("error"),
                    "latencyMs": round(latency_ms, 3),
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

    #: True once the model itself says it needs no tools. Only that makes the rule-based
    #: fallback appropriate; a loop that stopped for a budget or breaker must *not* be
    #: replaced by rules, or running out of budget would execute a tool the budget just
    #: refused to pay for.
    messages = build_messages(message, None)
    budget = max(1, settings.agent_max_tool_calls)
    #: Tools the failure contract has declared unusable for this turn. Kept so the
    #: tool list shrinks at most once per tool and the reason is stated once.
    withdrawn: set[str] = set()

    # The model that chooses tools is the model the caller asked for. Passing it explicitly
    # means the tool turn and the answer turn cannot disagree about which model is in use.
    model_token = set_model_override(state.get("model"))
    try:
        while candidates and rounds < max(1, settings.agent_max_rounds):
            await tick()
            try:
                turn: ModelTurn = await model_turn(
                    messages, candidates, emit=emit, streamer=streamer, poster=poster
                )
            except FunctionCallingUnavailable as error:
                logger.info("No model for tool choice; using rules: %s", error)
                stop_reason = stop_reason or tool_metrics.STOP_NO_MODEL
                break
            except Exception as error:  # pragma: no cover - defensive
                logger.warning("Tool calling failed (%s); using rules", error)
                stop_reason = stop_reason or tool_metrics.STOP_NO_MODEL
                break

            tokens, usage_source = _account_tokens(turn, messages, tokens)
            await tick()
            if settings.agent_token_budget and tokens > settings.agent_token_budget:
                stop_reason = tool_metrics.STOP_TOKEN_BUDGET
                if emit:
                    await emit({
                        "type": "thinking",
                        "content": f"已达到 token 预算（{tokens}/{settings.agent_token_budget}），停止继续调用工具。",
                        "step": True,
                    })
                break

            if turn.attempts > 1 and emit:
                # A retried model turn is worth saying out loud: the provider is degrading,
                # and the user is waiting longer than usual for a reason.
                await emit({
                    "type": "thinking",
                    "content": f"模型调用重试 {turn.attempts - 1} 次后成功（{', '.join(turn.retry_reasons)}）。",
                    "step": True,
                })

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
            # The result that goes back to the model is bounded by tokens; the event carries
            # the untrimmed text, so the trim is not a loss of evidence.
            for call, outcome in zip(batch, await run_round(batch, rounds)):
                messages.append(
                    tool_result_message(
                        call.call_id, outcome, tool_name=call.name
                    )
                )
                # Self-correction, enforced rather than hoped for. When the contract says
                # this tool cannot be used here (`change_tool`), leaving it in the candidate
                # list means the model can pick it again next round and burn the remaining
                # rounds against the same wall. Removing it makes the next decision
                # different in the only way that matters.
                #
                # Tell the model *why* in a system message: a silently shortened tool list
                # looks like a bug, and the model would try to explain the disappearance.
                if _advice_recovery(outcome) == "change_tool":
                    name = str(outcome.get("toolName") or call.name)
                    if name not in withdrawn:
                        withdrawn.add(name)
                        # Shrinking is a no-op when the tool was never offered (the
                        # common case, since the candidate list is already filtered),
                        # but it is the guard that keeps a laxer candidate source from
                        # re-offering something the contract just declared unusable.
                        candidates = [item for item in candidates if item != name]
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"工具 {name} 在当前场景不可用，已从可选工具中移除。"
                                    "请从剩余工具中选择，或直接回答用户；不要重复调用它。"
                                ),
                            }
                        )

            if pending_confirm:
                # Stop the loop here: the confirmation node decides whether these run, and
                # continuing would let the model pile more writes onto an unanswered prompt.
                stop_reason = stop_reason or tool_metrics.STOP_CONFIRMATION_PENDING
                break

            if budget <= 0:
                stop_reason = tool_metrics.STOP_TOOL_CALL_BUDGET
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
                stop_reason = tool_metrics.STOP_WRITE_SUCCEEDED
                if emit:
                    await emit({
                        "type": "thinking",
                        "content": "写操作已成功完成，停止继续调用工具。",
                        "step": True,
                    })
                break
    finally:
        reset_model_override(model_token)

    # A budget, breaker or confirmation stop must not execute rules after the fact: the loop
    # stopped for a reason, and turning that into a rule-based tool call would spend the very
    # budget that stopped it (and, for a confirm tier, bypass the confirmation). A model that
    # merely had nothing to call is also not the fallback path -- `mode` is "model" by then.
    #
    # `NO_MODEL` is the exception, and it is the case this fallback exists for: with no model
    # the rules are the only way a tool runs at all. Requiring `stop_reason is None` meant a
    # deployment without a model ran *no* tools while the log above claimed "using rules".
    fallback_allowed = stop_reason in (None, tool_metrics.STOP_NO_MODEL)
    if mode == "rule-based" and fallback_allowed:
        rounds = 1
        stop_reason = stop_reason or "RULE_BASED"
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
    elif mode == "rule-based":
        rounds = 0

    note_routing_mode(mode)
    tool_metrics.record_loop_stop(
        stop_reason or "COMPLETED", rounds=rounds, duration_ms=(time.perf_counter() - loop_started) * 1000.0
    )
    tool_metrics.record_loop_tokens(tokens, usage_source=usage_source)
    return results, mode, rounds, {
        "tokens": tokens or None,
        "usageSource": usage_source,
        "stopReason": stop_reason,
        "modelTurns": rounds if mode == "model" else 0,
        "pendingConfirmWrites": pending_confirm,
    }


def _clarification_for(
    results: list[dict[str, Any]],
) -> tuple[str | None, list[str]]:
    """The question to ask when a deterministic, user-fixable failure ended the turn.

    Only one case qualifies: **nothing succeeded** and at least one failure is one the user can
    resolve by answering (a bad identifier, a missing parameter, or a permission the account
    does not hold). A dependency failure is deliberately excluded -- asking the user to retype
    something while the service is down wastes their time and hides the outage.
    """
    if not results or any(result.get("success") for result in results):
        return None, []
    for result in results:
        error = str(result.get("error") or "")
        message = failures.recovery_message(error, str(result.get("text") or ""))
        if message:
            fields = [str(item) for item in (result.get("missingFields") or [])]
            if not fields:
                name = str(result.get("toolName") or "")
                if name:
                    fields = [name]
            return message, fields
    return None, []


async def execute_tools(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
    runtime: Runtime | None = None,
):
    """Run the tool loop for one turn.

    ``runtime`` is the framework's node runtime, used only to refresh the node's idle timer
    (`runtime.heartbeat()`). Without a heartbeat, a tool that legitimately runs for a minute
    looks exactly like a hung node to the `TimeoutPolicy` set in `root.py`; with it, the
    timeout measures silence rather than duration, which is what an idle timeout should mean.
    """
    emit = _emit(config)
    if state.get("action") == "capabilities":
        return {
            "tool_results": [],
            "knowledge_documents": [],
            "retrieved_documents": list(state.get("retrieved_documents", [])),
            "approval": state.get("approval"),
        }

    results, mode, rounds, accounting = await run_tool_loop(state, emit=emit, runtime=runtime)

    approval = state.get("approval")
    retrieved_documents = list(state.get("retrieved_documents", []))
    for result in results:
        if result.get("approval"):
            approval = result["approval"]
        for citation in result.get("citations", []) or []:
            retrieved_documents.append({"id": citation})

    question, missing = _clarification_for(results)

    return {
        "tool_results": results,
        "knowledge_documents": [],
        "retrieved_documents": retrieved_documents,
        "approval": approval,
        "tool_routing": mode,
        "tool_rounds": rounds,
        "tool_accounting": accounting,
        # Slot filling: a deterministic failure the user can fix ("that ticket number does not
        # exist", "you are not allowed to do that") ends the turn with a question instead of a
        # dead end. The user's answer arrives as the next message, so no resume machinery is
        # needed -- the conversation itself carries the fill-in.
        "clarification_required": bool(question),
        "clarifying_question": question or "",
        "missing_fields": missing,
        # The confirm-tier writes the loop refused to run. The graph's confirmation node
        # consumes this; nothing else may execute them, so the tier cannot be bypassed by
        # adding a different edge.
        "pending_confirm_writes": accounting.get("pendingConfirmWrites") or [],
    }


#: The tool the confirmation node presents to the user. It is a catalog name so the
#: approval record and the audit trail agree on what was confirmed.
CONFIRMATION_RULE_ID = "WRITE-CONFIRMATION-REQUIRED"


def _emit(config: RunnableConfig | None):
    """The event emitter, tolerating a missing or non-mapping config.

    Nodes are called directly in tests and from the resume path; either can hand in a config
    without a `configurable` mapping, and an `AttributeError` there would look like a node bug
    rather than a caller that did not set up a stream.
    """
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    return configurable.get("emit")


async def confirm_writes(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    """Pause on `confirm`-tier writes, execute the approved ones, and nothing else.

    Why this is a separate node rather than an ``interrupt()`` inside the loop: LangGraph
    re-enters an interrupted node from the top on resume, so an interrupt inside
    ``execute_tools`` would re-ask the model for its tool decision -- a second model call,
    at real cost, whose answer could differ from the one the user just confirmed. Here the
    interrupt is the *first* thing the node does, the pending calls were written to state
    by the previous node (so their ids are stable across the replay), and resuming runs
    straight through to execution with the decision in hand.

    The same protocol as ``approval_gate`` is used on purpose: one checkpoint/interrupt
    mechanism, one resume path, and one place where the Java side persists the pause.
    """
    from langgraph.types import interrupt

    emit = _emit(config)
    pending = list(state.get("pending_confirm_writes") or [])
    if not pending:
        return {}

    decisions = {
        str(item.get("callId")): str(item.get("decision") or "")
        for item in (state.get("write_confirmations") or [])
    }
    undecided = [call for call in pending if not decisions.get(str(call.get("callId")))]

    if not settings.write_confirmation_enabled:
        # The rollout switch: the tier degrades to `direct`. Every pending call is treated as
        # approved, because "confirmation is off" means "do not ask", not "ask and then refuse
        # everything" -- the second reading would be a silent loss of the action.
        for call in pending:
            decisions.setdefault(str(call.get("callId")), "approved")
    elif undecided:
        # First statement, before any side effect: this is the node re-entry contract.
        decision = interrupt(
            {
                "approval_id": "confirm_" + str(state.get("turn_id") or "")[:32],
                "turn_id": state.get("turn_id"),
                "user_id": state.get("user_id"),
                "agent_id": state.get("selected_agent"),
                "intent": state.get("intent"),
                "risk_level": "medium",
                "rule_id": CONFIRMATION_RULE_ID,
                # `requester` is the explicit, auditable value that means "the person who
                # asked may answer this one". Blank would also pass, but blank is
                # indistinguishable from "nobody thought about it".
                "required_approver_roles": "requester",
                "requested_action": ",".join(sorted({str(call.get("name")) for call in undecided})),
                "kind": "write_confirmation",
                "writes": [
                    {"callId": call.get("callId"), "name": call.get("name"), "args": call.get("args")}
                    for call in undecided
                ],
            }
        )
        verdict = ""
        if isinstance(decision, dict):
            verdict = str(decision.get("decision") or "")
        else:
            verdict = str(decision or "")
        approved = verdict in {"approve", "approved", "yes", "confirm"}
        for call in undecided:
            decisions[str(call.get("callId"))] = "approved" if approved else "rejected"
        if emit:
            await emit(
                {
                    "type": "thinking",
                    "content": (
                        "用户已确认写操作，开始执行。"
                        if approved
                        else "用户未确认写操作，已跳过执行。"
                    ),
                    "step": True,
                }
            )

    results, retrieved_documents = await _execute_confirmed(
        state, pending, decisions, emit=emit
    )
    return {
        "tool_results": results,
        "retrieved_documents": retrieved_documents,
        "write_confirmations": [
            {"callId": call_id, "decision": decision}
            for call_id, decision in decisions.items()
        ],
        "pending_confirm_writes": [],
        # The user just answered the only question that mattered here, so a clarification the
        # tool loop raised alongside the pending write must not outlive it -- otherwise the
        # turn would ask again about a failure the confirmation has already superseded.
        "clarification_required": False,
        "clarifying_question": "",
        "missing_fields": [],
    }


async def _execute_confirmed(
    state: dict[str, Any],
    pending: list[dict[str, Any]],
    decisions: dict[str, str],
    *,
    emit: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the approved calls through the same gates and the same ledger as the loop."""
    adapter = get_tool_adapter()
    breakers = default_registry()
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    results: list[dict[str, Any]] = []
    retrieved = list(state.get("retrieved_documents") or [])

    for call in pending:
        call_id = str(call.get("callId"))
        tool_name = str(call.get("name"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}

        if decisions.get(call_id) != "approved":
            results.append(
                {
                    "toolName": tool_name,
                    "success": False,
                    "text": "用户未确认该写操作，未执行。",
                    "error": "CONFIRMATION_REJECTED",
                }
            )
            tool_metrics.record_tool_call(
                tool_name, outcome=tool_metrics.OUTCOME_REFUSED, latency_ms=0.0
            )
            continue

        if tool_name not in TOOLS:
            continue
        if not authorize_tool(
            state.get("user_context"), str(state.get("selected_agent", "knowledge")), tool_name
        ):
            results.append(
                {
                    "toolName": tool_name,
                    "success": False,
                    "text": f"RBAC denied tool {tool_name} for current user roles",
                    "error": "RBAC_DENIED",
                }
            )
            tool_metrics.record_tool_call(
                tool_name, outcome=tool_metrics.OUTCOME_DENIED, latency_ms=0.0
            )
            continue

        digest = hashlib.sha256(_fingerprint(tool_name, args).encode("utf-8")).hexdigest()[:16]
        action = f"{tool_name}#{digest}"[:64]

        async def run(provider_key: str, _name=tool_name, _args=args):
            token = set_idempotency_key(provider_key)
            try:
                return await adapter.execute(_name, _args, state)
            finally:
                reset_idempotency_key(token)

        started = time.perf_counter()
        try:
            outcome = await effect_ledger.run_once(
                turn_id=turn_id, action=action, approval_id=None, execute=run
            )
        except Exception as error:
            logger.warning("Confirmed write %s raised: %s", tool_name, error)
            breakers.breaker(tool_name).record_failure(error, transport=_is_transport(error))
            outcome = {
                "toolName": tool_name,
                "success": False,
                "text": f"工具执行异常：{error}",
            }
        latency_ms = (time.perf_counter() - started) * 1000.0
        outcome = apply_result_contract(tool_name, {**outcome, "toolName": tool_name})
        results.append(outcome)
        _record_breaker(breakers, tool_name, outcome)
        _record_metrics(tool_name, outcome, latency_ms)
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": call_id,
                    "name": tool_name,
                    "success": outcome.get("success", False),
                    "message": outcome.get("text", ""),
                    "attempts": outcome.get("attempts", 1),
                    "errorCode": outcome.get("error"),
                    "latencyMs": round(latency_ms, 3),
                }
            )
    return results, retrieved
