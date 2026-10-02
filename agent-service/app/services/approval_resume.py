import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.graph.runner import (
    TERMINAL_CHECKPOINT_ERRORS,
    ResumeOutcome,
    resume_graph,
)
from app.persistence.approval_store import (
    ApprovalStoreUnavailable,
    ApprovalTask,
    approval_store,
)

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ApprovalResolution:
    status: Literal[
        "resumed",
        "already_resumed",
        "interrupted",
        "failed",
    ]
    error: str | None = None
    detail: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def retryable(self) -> bool:
        return self.status == "failed" and self.error == "RESUME_FAILED"


async def _noop(_: dict[str, Any]) -> None:
    return None


async def resolve_approval(
    task: ApprovalTask,
    *,
    graph,
    resume_request_id: str | None = None,
    emit: Emit | None = None,
) -> ApprovalResolution:
    """Apply one approval decision to the LangGraph run, exactly once.

    Ordering matters: the resume request id and the checkpoint reference are
    persisted *before* the graph is resumed, and the attempt is claimed
    atomically so a duplicate HTTP call, a reconciler tick or a second replica
    cannot deliver the same resume twice.
    """
    emit = emit or _noop
    request_id = resume_request_id or task.resume_request_id or f"rsq_{uuid.uuid4().hex}"

    if task.resume_state == "succeeded":
        logger.info("Approval %s was already resumed; returning idempotent result", task.id)
        return ApprovalResolution(status="already_resumed")

    if not task.checkpoint_id and not task.interrupt_id:
        await approval_store.mark_resume_result(
            task.id,
            resume_state="stale",
            resume_error="MISSING_CHECKPOINT_REFERENCE",
        )
        return ApprovalResolution(
            status="failed",
            error="MISSING_CHECKPOINT_REFERENCE",
        )

    try:
        claimed = await approval_store.claim_resume(
            task.id,
            resume_request_id=request_id,
        )
    except ApprovalStoreUnavailable as exc:
        return ApprovalResolution(
            status="failed",
            error="APPROVAL_STORE_UNAVAILABLE",
            detail=str(exc),
        )

    if claimed is None:
        # Another attempt owns this approval. Report the stored state instead of
        # racing it, and let the caller retry later.
        current = await approval_store.get(task.id)
        if current is not None and current.resume_state == "succeeded":
            return ApprovalResolution(status="already_resumed")
        return ApprovalResolution(
            status="failed",
            error="RESUME_IN_PROGRESS",
            detail="another resume attempt owns this approval",
        )

    decision = {
        "decision": "approved" if claimed.status == "approved" else "rejected",
        "approval_id": claimed.id,
        "checkpoint_id": claimed.checkpoint_id,
        "checkpoint_ns": claimed.checkpoint_ns,
        "interrupt_id": claimed.interrupt_id,
        "resume_request_id": claimed.resume_request_id or request_id,
    }
    turn_id = claimed.turn_id or claimed.run_id

    outcome: ResumeOutcome = await resume_graph(
        turn_id,
        decision,
        emit,
        graph=graph,
    )

    if outcome.status in {"resumed", "interrupted"}:
        await approval_store.mark_resume_result(
            claimed.id,
            resume_state="succeeded",
            consumed=outcome.status == "resumed",
            resume_error=None,
        )
        await approval_store.set_result(
            claimed.id,
            {
                "resume_status": outcome.status,
                "events": outcome.events,
            },
        )
        return ApprovalResolution(
            status=outcome.status,
            events=outcome.events,
        )

    if outcome.error in TERMINAL_CHECKPOINT_ERRORS:
        # The checkpoint is gone or superseded. Retrying cannot help and must
        # never be turned into a fresh run.
        await approval_store.mark_resume_result(
            claimed.id,
            resume_state="stale",
            resume_error=outcome.error,
        )
        return ApprovalResolution(
            status="failed",
            error=outcome.error,
            detail=outcome.detail,
            events=outcome.events,
        )

    await approval_store.mark_resume_result(
        claimed.id,
        resume_state="failed",
        resume_error=outcome.error or "RESUME_FAILED",
    )
    return ApprovalResolution(
        status="failed",
        error=outcome.error or "RESUME_FAILED",
        detail=outcome.detail,
        events=outcome.events,
    )
