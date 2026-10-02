import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.audit import record_action
from app.core.rate_limit import enforce_rate_limit
from app.core.security import Caller
from app.config import settings
from app.graph.runner import TERMINAL_CHECKPOINT_ERRORS
from app.persistence.approval_store import (
    ApprovalPolicyError,
    ApprovalStoreUnavailable,
    approval_store,
)
from app.services.approval_resume import ApprovalResolution, resolve_approval

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

_decide_limiter = enforce_rate_limit(
    "approvals.decide", settings.rate_limit_approval_per_minute
)
_list_limiter = enforce_rate_limit(
    "approvals.list", settings.rate_limit_default_per_minute
)


@router.get("")
async def list_approvals(caller: Caller = Depends(_list_limiter)):
    try:
        tasks = await approval_store.list_by_user(caller.user_id)
    except ApprovalStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="APPROVAL_STORE_UNAVAILABLE") from exc
    return {"approvals": tasks}


def _resolution_response(
    task: Any,
    resolution: ApprovalResolution,
) -> JSONResponse:
    body = {
        "approval": _serialize(task),
        "resume": {
            "status": resolution.status,
            "error": resolution.error,
            "detail": resolution.detail,
            "retryable": resolution.retryable,
        },
    }
    if resolution.error in TERMINAL_CHECKPOINT_ERRORS:
        # A missing or superseded checkpoint is terminal: the caller must not
        # retry and no new run may be started.
        return JSONResponse(status_code=409, content=body)
    if resolution.status == "failed":
        return JSONResponse(status_code=202, content=body)
    return JSONResponse(status_code=200, content=body)


def _serialize(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "run_id": task.run_id,
        "turn_id": task.turn_id,
        "thread_id": task.thread_id,
        "status": task.status,
        "resume_state": task.resume_state,
        "resume_attempts": task.resume_attempts,
        "resume_error": task.resume_error,
        "resume_request_id": task.resume_request_id,
        "checkpoint_id": task.checkpoint_id,
        "checkpoint_ns": task.checkpoint_ns,
        "interrupt_id": task.interrupt_id,
        "consumed_at": task.consumed_at,
        "decided_by": task.decided_by,
        "decided_at": task.decided_at,
        "decision_comment": task.decision_comment,
        "required_approver_roles": task.required_approver_roles,
    }


@router.patch("/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    payload: dict[str, Any],
    request: Request,
    caller: Caller = Depends(_decide_limiter),
):
    action = payload.get("action")
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="action must be approve or reject")

    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="GRAPH_NOT_READY")

    requested_status = "approved" if action == "approve" else "rejected"
    comment = payload.get("comment")
    # A client-supplied request id makes the delivery idempotent across retries;
    # generating one keeps single-shot callers working.
    resume_request_id = str(
        payload.get("resume_request_id") or f"rsq_{uuid.uuid4().hex}"
    )

    try:
        decision = await approval_store.decide(
            approval_id,
            requested_status,
            resume_request_id=resume_request_id,
            user_id=caller.user_id,
            actor_roles=caller.roles,
            comment=str(comment) if comment else None,
            source_ip=caller.source_ip,
        )
    except ApprovalPolicyError as exc:
        # Separation of duties / required approver role. Record the attempt: a
        # refused self-approval is exactly the event an auditor looks for.
        await record_action(
            "approval.decide",
            caller=caller,
            object_type="approval",
            object_id=approval_id,
            outcome="denied",
            detail={"reason": exc.code, "requested_status": requested_status},
        )
        raise HTTPException(status_code=403, detail=exc.code) from exc
    except ApprovalStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="APPROVAL_STORE_UNAVAILABLE") from exc

    if decision.reason == "not_found" or decision.task is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    task = decision.task

    if decision.won:
        await record_action(
            "approval.decide",
            caller=caller,
            object_type="approval",
            object_id=approval_id,
            outcome="success",
            detail={
                "decision": requested_status,
                "resume_request_id": resume_request_id,
                "has_comment": bool(comment),
            },
        )
        resolution = await resolve_approval(
            task,
            graph=graph,
            resume_request_id=resume_request_id,
        )
        return _resolution_response(task, resolution)

    # Repeat click. Never run the side effect twice: report the current state, and
    # only re-drive the resume when the earlier attempt did not finish. Reusing
    # the persisted resume_request_id keeps the agent side idempotent.
    if task.status != requested_status:
        raise HTTPException(
            status_code=409,
            detail=f"APPROVAL_ALREADY_DECIDED:{task.status}",
        )

    if task.resume_state == "succeeded":
        return _resolution_response(
            task,
            ApprovalResolution(status="already_resumed"),
        )

    if task.resume_state in {"queued", "failed"}:
        resolution = await resolve_approval(
            task,
            graph=graph,
            resume_request_id=task.resume_request_id,
        )
        return _resolution_response(task, resolution)

    raise HTTPException(
        status_code=409,
        detail=f"RESUME_NOT_RETRYABLE:{task.resume_state}",
    )


@router.get("/{approval_id}/decisions")
async def list_decisions(
    approval_id: str,
    caller: Caller = Depends(_decide_limiter),
):
    """Append-only decision trail for one approval (who decided what, when).

    Not scoped to the requester: an approver legitimately acts on, and must be
    able to read, approvals they did not raise.
    """
    try:
        task = await approval_store.get(approval_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        trail = await approval_store.list_decisions(approval_id)
    except ApprovalStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail="APPROVAL_STORE_UNAVAILABLE") from exc
    return {
        "approval": _serialize(task),
        "required_approver_roles": task.required_approver_roles,
        "decisions": trail,
    }
