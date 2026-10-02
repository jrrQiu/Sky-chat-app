import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.api.schemas import ChatRequest
from app.gateway.litellm import generate_title
from app.persistence.approval_store import ApprovalStoreUnavailable, approval_store


Emit = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)


@dataclass
class ResumeOutcome:
    """Structured result of one resume attempt.

    The HTTP layer maps `error` to a status code, and the caller decides whether
    the attempt may be retried: checkpoint errors are terminal, everything else
    is retryable with the same `resume_request_id`.
    """

    status: Literal["resumed", "already_resumed", "interrupted", "failed"]
    error: str | None = None
    detail: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def retryable(self) -> bool:
        return self.status == "failed" and self.error == "RESUME_FAILED"


TERMINAL_CHECKPOINT_ERRORS = {
    "CHECKPOINT_NOT_FOUND",
    "STALE_APPROVAL",
    "MISSING_CHECKPOINT_REFERENCE",
}


def initial_state(request: ChatRequest) -> dict[str, Any]:
    turn_id = request.request_id
    envelope = request.context_envelope
    return {
        "turn_id": turn_id,
        "execution_id": f"exec_{uuid.uuid4().hex}",
        "request_id": turn_id,
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
        "context_envelope": envelope.model_dump() if envelope else {},
        "context_blocks": (
            [block.model_dump() for block in envelope.blocks] if envelope else []
        ),
        "context_recent_messages": (
            list(envelope.recent_messages) if envelope else []
        ),
        "latest_user_message": request.latest_user_message,
        "model": request.model,
        "api_key": request.api_key or "",
        "enable_thinking": request.enable_thinking,
        "enable_web_search": request.enable_web_search,
        "summary": "",
        "intent": request.agent_state.intent,
        "domain": "",
        "operation": "",
        "intent_confidence": 0.0,
        "intent_source": "",
        "intent_resolution": {},
        "action": request.agent_state.slots.get("action", "query"),
        "selected_agent": request.agent_state.slots.get(
            "selected_agent", "knowledge"
        ),
        "risk_level": request.agent_state.riskLevel,
        "plan": request.agent_state.plan,
        "slots": {},
        "missing_fields": request.agent_state.missingFields,
        "retrieved_documents": list(request.agent_state.retrievedDocuments),
        "knowledge_documents": [],
        "knowledge_context": "",
        "tool_results": list(request.agent_state.toolResults),
        "approval": request.agent_state.approval,
        "pending_action": "",
        "executed_writes": [],
        "status": "running",
        "guard_error": None,
        "final_answer": "",
        "context_report": {},
        "context_hash": "",
        "requires_clarification": False,
        "clarifying_question": "",
    }


def _config(turn_id: str, emit: Emit | None) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": turn_id,
            "emit": emit,
        }
    }


def _resume_config(
    turn_id: str,
    decision: dict[str, Any],
    emit: Emit,
) -> RunnableConfig:
    config = _config(turn_id, emit)
    checkpoint_id = decision.get("checkpoint_id")
    checkpoint_ns = decision.get("checkpoint_ns")
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = str(checkpoint_id)
    if checkpoint_ns is not None:
        config["configurable"]["checkpoint_ns"] = str(checkpoint_ns)
    return config


async def _emit_terminal(emit: Emit) -> None:
    try:
        await emit({"type": "complete"})
    except Exception:
        logger.exception("Failed to emit terminal complete event")


async def run_graph(request: ChatRequest, emit: Emit, graph=None) -> None:
    if graph is None:
        raise RuntimeError("A compiled graph instance is required")
    turn_id = request.request_id
    config = _config(turn_id, emit)
    interrupted = False
    final_action = None

    try:
        async for chunk in graph.astream(
            initial_state(request),
            config=config,
            stream_mode="updates",
        ):
            if "__interrupt__" in chunk:
                interrupted = True
                interrupt_value = chunk["__interrupt__"][0].value
                checkpoint_refs = await _checkpoint_refs(graph, config, interrupt_value)
                event = {
                    "type": "approval_required",
                    **interrupt_value,
                    **checkpoint_refs,
                }
                approval_id = interrupt_value.get("approval_id")
                if approval_id:
                    # An approval without a persisted checkpoint reference can
                    # never be resumed, so the client must not be told that one
                    # is awaiting a decision.
                    try:
                        await approval_store.update_checkpoint_refs(
                            approval_id,
                            checkpoint_id=checkpoint_refs.get("checkpoint_id", ""),
                            checkpoint_ns=checkpoint_refs.get("checkpoint_ns", ""),
                            interrupt_id=checkpoint_refs.get("interrupt_id", ""),
                        )
                    except ApprovalStoreUnavailable:
                        logger.exception(
                            "Could not persist checkpoint refs for %s", approval_id
                        )
                        interrupted = False
                        await emit(
                            {
                                "type": "error",
                                "message": "审批状态存储不可用，已中止本次高风险操作",
                            }
                        )
                        return
                await emit(event)
                break

        if not interrupted:
            snapshot = await graph.aget_state(config)
            final_action = (
                snapshot.values.get("action") if snapshot and snapshot.values else None
            )

            history_count = (
                len(request.context_envelope.recent_messages)
                if request.context_envelope is not None
                else len(request.messages)
            )
            if history_count <= 1 and final_action != "capabilities":
                title = await generate_title(
                    request.latest_user_message,
                    api_key=request.api_key,
                )
                if title != "新对话":
                    await emit({"type": "conversation_title", "content": title})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Agent graph execution failed")
        try:
            await emit(
                {
                    "type": "error",
                    "message": f"Agent 执行失败：{exc}",
                }
            )
        except Exception:
            logger.exception("Failed to emit agent error event")
    finally:
        if not interrupted:
            await _emit_terminal(emit)


async def resume_graph(
    turn_id: str,
    decision: dict[str, Any],
    emit: Emit,
    graph=None,
) -> ResumeOutcome:
    """Resume an interrupted run at an exact checkpoint.

    The resume value is only delivered after the checkpoint precheck passes:
    without it a lost checkpoint would silently start a brand new run and report
    success, which is the failure mode this protocol exists to prevent.
    """
    if graph is None:
        raise RuntimeError("A compiled graph instance is required")

    checkpoint_error = await validate_resume_checkpoint(graph, turn_id, decision, emit)
    if checkpoint_error:
        await emit({"type": "error", "message": checkpoint_error})
        return ResumeOutcome(status="failed", error=checkpoint_error)

    config = _resume_config(turn_id, decision, emit)
    events: list[dict[str, Any]] = []
    interrupted = False

    try:
        resume_value = {"decision": decision.get("decision")}
        async for chunk in graph.astream(
            Command(resume=resume_value),
            config=config,
            stream_mode="updates",
        ):
            if "__interrupt__" in chunk:
                interrupted = True
                interrupt_value = chunk["__interrupt__"][0].value
                checkpoint_refs = await _checkpoint_refs(graph, config, interrupt_value)
                event = {
                    "type": "approval_required",
                    **interrupt_value,
                    **checkpoint_refs,
                }
                approval_id = interrupt_value.get("approval_id")
                if approval_id:
                    try:
                        await approval_store.update_checkpoint_refs(
                            approval_id,
                            checkpoint_id=checkpoint_refs.get("checkpoint_id", ""),
                            checkpoint_ns=checkpoint_refs.get("checkpoint_ns", ""),
                            interrupt_id=checkpoint_refs.get("interrupt_id", ""),
                        )
                    except ApprovalStoreUnavailable:
                        logger.exception(
                            "Could not persist checkpoint refs for %s", approval_id
                        )
                        await emit(
                            {
                                "type": "error",
                                "message": "审批状态存储不可用，已中止本次高风险操作",
                            }
                        )
                        return ResumeOutcome(
                            status="failed",
                            error="APPROVAL_STORE_UNAVAILABLE",
                            events=events,
                        )
                events.append(event)
                await emit(event)
                break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Workflow resume failed")
        await emit({"type": "error", "message": f"审批恢复失败：{exc}"})
        return ResumeOutcome(
            status="failed",
            error="RESUME_FAILED",
            detail=str(exc),
            events=events,
        )
    finally:
        if not interrupted:
            await _emit_terminal(emit)
            events.append({"type": "complete"})

    return ResumeOutcome(
        status="interrupted" if interrupted else "resumed",
        events=events,
    )


async def validate_resume_checkpoint(
    graph,
    turn_id: str,
    decision: dict[str, Any],
    emit: Emit | None = None,
) -> str | None:
    """Precheck a resume against the live and the pinned checkpoint.

    Returns `None` when the resume may proceed, otherwise a machine-readable
    error code. `emit` is accepted for symmetry with the streaming helpers and is
    not required.
    """
    expected_checkpoint = decision.get("checkpoint_id")
    expected_ns = decision.get("checkpoint_ns")
    expected_interrupt = decision.get("interrupt_id")
    if not expected_checkpoint and not expected_interrupt:
        return "MISSING_CHECKPOINT_REFERENCE"

    base_config = _config(turn_id, None)
    snapshot = await graph.aget_state(base_config)
    if not snapshot:
        return "CHECKPOINT_NOT_FOUND"
    if not snapshot.next or not snapshot.tasks:
        # Nothing is pending: either the run finished here or it was already
        # resumed. Either way a new run must not be started.
        return "STALE_APPROVAL"

    snapshot_config = snapshot.config.get("configurable", {})
    actual_checkpoint = str(snapshot_config.get("checkpoint_id") or "")
    actual_ns = str(snapshot_config.get("checkpoint_ns") or "")
    actual_interrupt = _first_interrupt_id(snapshot)

    if expected_checkpoint and expected_checkpoint != actual_checkpoint:
        # The thread moved on since the approval was recorded. Re-read the pinned
        # checkpoint to tell "superseded" apart from "gone".
        pinned = await _state_at_checkpoint(
            graph,
            turn_id,
            str(expected_checkpoint),
            expected_ns,
        )
        if pinned is None:
            return "CHECKPOINT_NOT_FOUND"
        if not pinned.next or not pinned.tasks:
            return "STALE_APPROVAL"
        pinned_interrupt = _first_interrupt_id(pinned)
        if expected_interrupt and pinned_interrupt and (
            expected_interrupt != pinned_interrupt
        ):
            return "STALE_APPROVAL"
        return None

    if expected_ns is not None and expected_ns != actual_ns:
        return "STALE_APPROVAL"
    if expected_interrupt and expected_interrupt != actual_interrupt:
        return "STALE_APPROVAL"
    return None


def _first_interrupt_id(snapshot) -> str:
    for task in getattr(snapshot, "tasks", ()) or ():
        interrupts = getattr(task, "interrupts", ()) or ()
        if interrupts:
            return str(interrupts[0].id)
    return ""


async def _state_at_checkpoint(
    graph,
    turn_id: str,
    checkpoint_id: str,
    checkpoint_ns: Any,
):
    config: RunnableConfig = {
        "configurable": {
            "thread_id": turn_id,
            "checkpoint_id": checkpoint_id,
        }
    }
    if checkpoint_ns is not None:
        config["configurable"]["checkpoint_ns"] = str(checkpoint_ns)
    try:
        return await graph.aget_state(config)
    except Exception:
        logger.warning(
            "Pinned checkpoint %s for %s could not be read",
            checkpoint_id,
            turn_id,
            exc_info=True,
        )
        return None


async def _checkpoint_refs(
    graph,
    config: RunnableConfig,
    interrupt_value: dict[str, Any],
) -> dict[str, str]:
    snapshot = await graph.aget_state(config)
    if not snapshot or not snapshot.tasks:
        return {}

    snapshot_config = snapshot.config.get("configurable", {})
    interrupt_id = ""
    if snapshot.tasks:
        interrupts = getattr(snapshot.tasks[0], "interrupts", ())
        interrupt_id = str(interrupts[0].id) if interrupts else ""

    return {
        "checkpoint_id": str(snapshot_config.get("checkpoint_id") or ""),
        "checkpoint_ns": str(snapshot_config.get("checkpoint_ns") or ""),
        "interrupt_id": interrupt_id,
    }


def format_sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return f"event: message\ndata: {data}\n\n"
