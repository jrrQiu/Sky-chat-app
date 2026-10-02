import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.core.audit import record_action
from app.core.rate_limit import enforce_rate_limit
from app.core.security import Caller
from app.graph.runner import (
    TERMINAL_CHECKPOINT_ERRORS,
    ResumeOutcome,
    format_sse,
    resume_graph,
    validate_resume_checkpoint,
)
from app.persistence.approval_store import ApprovalStoreUnavailable, approval_store
from app.persistence.redis_streams import RedisEventStream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])

_resume_limiter = enforce_rate_limit(
    "approvals.resume", settings.rate_limit_approval_per_minute
)


def _coerce_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise the resume payload.

    `decision` may be a bare string (frontend style) or an object carrying the
    checkpoint reference (service-to-service style). Checkpoint fields are only
    ever read back from the graph snapshot and echoed by the caller; a client
    cannot invent them into existence because the precheck compares them against
    live state.
    """
    raw_decision = payload.get("decision")
    if isinstance(raw_decision, dict):
        decision = dict(raw_decision)
    else:
        decision = {"decision": str(raw_decision or "")}

    for key in (
        "approval_id",
        "checkpoint_id",
        "checkpoint_ns",
        "interrupt_id",
        "resume_request_id",
    ):
        if key in payload and key not in decision:
            decision[key] = payload[key]

    return decision


def _wants_sse(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return False
    return "text/event-stream" in accept


async def _record_resume_outcome(
    decision: dict[str, Any],
    outcome: ResumeOutcome,
) -> None:
    """Mirror the graph result onto the approval row.

    Without this the row stays `queued` after a successful resume, and the
    reconciler would retry it until it read the consumed checkpoint and (wrongly)
    marked the approval stale. Only the resume fields are touched: the approval
    status stays owned by whichever service decided it.
    """
    approval_id = decision.get("approval_id")
    if not approval_id:
        return
    try:
        if outcome.status in {"resumed", "interrupted"}:
            await approval_store.mark_resume_result(
                str(approval_id),
                resume_state="succeeded",
                consumed=outcome.status == "resumed",
            )
        elif outcome.error in TERMINAL_CHECKPOINT_ERRORS:
            await approval_store.mark_resume_result(
                str(approval_id),
                resume_state="stale",
                resume_error=outcome.error,
            )
        else:
            await approval_store.mark_resume_result(
                str(approval_id),
                resume_state="failed",
                resume_error=outcome.error or "RESUME_FAILED",
            )
    except ApprovalStoreUnavailable:
        # The graph already advanced; only the bookkeeping failed. Log and let the
        # reconciler converge rather than failing a successful resume.
        logger.exception("Could not record resume outcome for %s", approval_id)


@router.post("/{turn_id}/resume")
async def resume_workflow(
    turn_id: str,
    payload: dict[str, Any],
    request: Request,
    caller: Caller = Depends(_resume_limiter),
):
    decision = _coerce_decision(payload)
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="GRAPH_NOT_READY")

    async def noop(_: dict[str, Any]) -> None:
        return None

    checkpoint_error = await validate_resume_checkpoint(
        graph,
        turn_id,
        decision,
        noop,
    )
    if checkpoint_error:
        # Returning 409 instead of resuming is what prevents a lost checkpoint
        # from silently becoming a brand new run that reports success.
        status_code = 409 if checkpoint_error in TERMINAL_CHECKPOINT_ERRORS else 400
        await record_action(
            "approval.resume",
            caller=caller,
            object_type="turn",
            object_id=turn_id,
            outcome="denied",
            detail={"reason": checkpoint_error},
        )
        raise HTTPException(status_code=status_code, detail=checkpoint_error)

    if not _wants_sse(request):
        events: list[dict[str, Any]] = []

        async def collect(event: dict[str, Any]) -> None:
            events.append(event)

        outcome = await resume_graph(turn_id, decision, collect, graph=graph)
        await _record_resume_outcome(decision, outcome)
        await record_action(
            "approval.resume",
            caller=caller,
            object_type="turn",
            object_id=turn_id,
            outcome="success" if outcome.status != "failed" else "failure",
            detail={
                "status": outcome.status,
                "error": outcome.error,
                "approval_id": decision.get("approval_id"),
                "resume_request_id": decision.get("resume_request_id"),
            },
        )
        if outcome.status == "failed":
            raise HTTPException(
                status_code=409,
                detail=outcome.error or "RESUME_FAILED",
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": outcome.status,
                "turn_id": turn_id,
                "resume_request_id": decision.get("resume_request_id"),
                "events": events,
            },
        )

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    redis_stream = RedisEventStream()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def event_generator():
        task = asyncio.create_task(
            resume_graph(turn_id, decision, emit, graph=graph)
        )
        terminal_seen = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=settings.stream_idle_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    error = {"type": "error", "message": "RESUME_TIMEOUT"}
                    await redis_stream.publish(f"run:{turn_id}", error)
                    yield format_sse(error)
                    break

                await redis_stream.publish(f"run:{turn_id}", event)
                yield format_sse(event)
                if event.get("type") in {"complete", "approval_required"}:
                    terminal_seen = True
                    break
                if event.get("type") == "error":
                    # A terminal precheck/resume failure never emits `complete`.
                    break
        finally:
            if not task.done():
                task.cancel()
            else:
                try:
                    await _record_resume_outcome(decision, task.result())
                except Exception:
                    logger.exception("Resume task failed for %s", turn_id)
            if not terminal_seen:
                complete = {"type": "complete"}
                await redis_stream.publish(f"run:{turn_id}", complete)
                yield format_sse(complete)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
