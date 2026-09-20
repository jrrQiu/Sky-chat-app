from typing import Any

from app.persistence.approval_store import ApprovalTask, approval_store
from app.tools.adapters.mock import execute_mock_tool


async def resume_after_approval(task: ApprovalTask) -> dict[str, Any]:
    pending_action = task.resume_payload.get("pending_action")
    if not pending_action:
        return {"resumed": False, "reason": "No pending action"}

    state = {
        "request_id": task.resume_payload.get("request_id", task.run_id),
        "user_id": task.user_id,
        "intent": task.intent,
        "risk_level": task.risk_level,
        "action": "approve",
        "selected_agent": task.agent_id,
    }

    result = await execute_mock_tool(
        str(pending_action),
        {"latest_user_message": ""},
        state,
    )
    await approval_store.set_result(task.id, result)
    return {"resumed": True, "action": pending_action, "result": result}
