from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_internal_token
from app.persistence.approval_store import approval_store
from app.services.approval_resume import resume_after_approval

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(user_id: str = Depends(require_internal_token)):
    return {"approvals": await approval_store.list_by_user(user_id)}


@router.patch("/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    payload: dict,
    user_id: str = Depends(require_internal_token),
):
    action = payload.get("action")
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="action must be approve or reject")

    task = await approval_store.get(approval_id)
    if not task or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Approval not found")

    decision = "approved" if action == "approve" else "rejected"
    updated = await approval_store.decide(approval_id, decision)
    if not updated:
        raise HTTPException(status_code=409, detail="Approval is already decided")

    resumed = await resume_after_approval(updated) if decision == "approved" else None
    return {"approval": updated, "resume": resumed}
