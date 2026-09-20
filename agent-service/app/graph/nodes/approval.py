import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.persistence.approval_store import approval_store


async def create_approval(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    if state.get("approval") or state.get("risk_level") not in {"high"}:
        return {}

    emit = config.get("configurable", {}).get("emit") if config else None
    selected_agent = str(state.get("selected_agent", "approval"))
    pending_action = {
        "network": "vpn_api_provision",
        "finance": "finance_submit",
        "approval": "approval_decision",
    }.get(selected_agent, "approval_decision")
    task = await approval_store.create(
        task_id=f"approval_{int(time.time() * 1000)}",
        run_id=str(state.get("request_id", "")),
        user_id=str(state.get("user_id", "")),
        agent_id=selected_agent,
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        rule_id="HIGH-RISK-APPROVAL-REQUIRED",
        resume_payload={
            "pending_action": pending_action,
            "request_id": str(state.get("request_id", "")),
        },
    )

    if emit:
        await emit(
            {
                "type": "tool_result",
                "toolCallId": f"approval_{task.id}",
                "name": "temporal",
                "success": True,
                "message": f"长流程审批任务已创建：{task.id}",
            }
        )

    return {
        "approval": {
            "id": task.id,
            "status": "pending",
            "required": True,
            "ruleId": task.rule_id,
        }
    }
