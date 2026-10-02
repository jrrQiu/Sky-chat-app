import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.core.audit import record_action
from app.core.effect_ledger import effect_key_for, effect_ledger
from app.core.guardrails import enforce_tool_rails
from app.persistence.approval_store import (
    approval_id_for,
    approval_key_for,
    approval_store,
)
from app.registry.rbac import authorize_agent, authorize_tool
from app.tools.adapters import get_tool_adapter


# Who may approve a high-risk action, per requesting agent. Enforced on both
# sides of the wire: Java checks it at the decision endpoint, and the agent
# store re-checks it so the internal route cannot be bypassed.
APPROVER_ROLES_BY_AGENT: dict[str, str] = {
    "network": "network_admin,approver",
    "finance": "approver,auditor",
    "it": "it_staff,approver",
    "hr": "hr_staff,approver",
    "approval": "approver",
}
DEFAULT_APPROVER_ROLES = "approver"


def approver_roles_for(agent_id: str) -> str:
    return APPROVER_ROLES_BY_AGENT.get(agent_id, DEFAULT_APPROVER_ROLES)


def _emit(config: RunnableConfig | None):
    if not config:
        return None
    return config.get("configurable", {}).get("emit")


async def collect_slots(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "正在整理请求所需的业务字段。",
                "step": True,
            }
        )
    return {
        "slots": state.get("slots", {}),
        "missing_fields": [],
    }


async def validate_permissions(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    selected_agent = str(state.get("selected_agent", "knowledge"))
    if not authorize_agent(state.get("user_context"), selected_agent):
        message = f"当前用户无权发起 {selected_agent} 请求"
        if emit:
            await emit({"type": "error", "message": message})
        return {
            "status": "failed",
            "guard_error": message,
            "final_answer": message,
        }

    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "权限校验通过，开始准备只读数据。",
                "step": True,
            }
        )
    return {}


async def prepare_read_only_tools(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    selected_agent = str(state.get("selected_agent", "knowledge"))
    read_only_by_agent = {
        "network": ["cmdb", "permission_system"],
        "finance": ["erp"],
        "hr": ["hris"],
        "it": ["idp"],
        "approval": [],
    }
    tool_names = read_only_by_agent.get(selected_agent, [])
    adapter = get_tool_adapter()
    new_results: list[dict[str, Any]] = []
    new_documents: list[dict[str, Any]] = []

    for tool_name in tool_names:
        if not authorize_tool(state.get("user_context"), selected_agent, tool_name):
            result = {
                "toolName": tool_name,
                "success": False,
                "text": f"RBAC denied tool {tool_name} for current user roles",
            }
            new_results.append(result)
            if emit:
                await emit(
                    {
                        "type": "tool_result",
                        "toolCallId": f"tool_{uuid.uuid4().hex[:12]}",
                        "name": tool_name,
                        "success": False,
                        "message": result["text"],
                    }
                )
            continue

        tool_call_id = f"tool_{uuid.uuid4().hex[:12]}"
        args = {
            "request_id": state.get("request_id"),
            "user_id": state.get("user_id"),
            "intent": state.get("intent"),
            "risk_level": state.get("risk_level"),
            "latest_user_message": state.get("latest_user_message"),
        }
        if emit:
            await emit(
                {
                    "type": "tool_call",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "args": args,
                }
            )

        result = await adapter.execute(tool_name, args, state)
        result = {**result, "toolName": tool_name}
        new_results.append(result)

        if result.get("citations"):
            new_documents.extend(
                {"id": citation} for citation in result["citations"]
            )

        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "success": result.get("success", False),
                    "message": result.get("text", ""),
                    "resultCount": len(result.get("citations", [])),
                    "sources": [
                        {"title": citation, "url": ""}
                        for citation in result.get("citations", [])
                    ],
                }
            )

    return {
        "tool_results": new_results,
        "knowledge_documents": new_documents,
        "retrieved_documents": [
            *state.get("retrieved_documents", []),
            *new_documents,
        ],
    }


async def approval_gate(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    if state.get("risk_level") != "high":
        return {}

    emit = _emit(config)
    approval = state.get("approval") or {}
    approval_id = str(approval.get("id") or "")
    if not approval_id:
        return {
            "status": "failed",
            "guard_error": "审批任务缺失",
        }

    decision = interrupt(
        {
            "type": "approval_required",
            "approval_id": approval_id,
            "risk_level": state.get("risk_level"),
            "requested_action": state.get("pending_action"),
            "agent_id": state.get("selected_agent"),
            "intent": state.get("intent"),
            "rule_id": approval.get("ruleId"),
            "required_approver_roles": approval.get("requiredApproverRoles")
            or approver_roles_for(str(state.get("selected_agent", "approval"))),
            "user_id": state.get("user_id"),
            "turn_id": state.get("turn_id") or state.get("request_id"),
        }
    )

    if decision.get("decision") != "approved":
        return {
            "approval": {
                "id": approval_id,
                "status": "rejected",
                "required": True,
                "ruleId": approval.get("ruleId"),
            },
            "status": "failed",
            "guard_error": "审批被拒绝",
        }

    return {
        "approval": {
            "id": approval_id,
            "status": "approved",
            "required": True,
            "ruleId": approval.get("ruleId"),
        },
        "status": "resuming",
    }


async def prepare_approval(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    """Create the approval task.

    This node owns the approval side effect so that `approval_gate` — which is
    the node LangGraph re-enters on resume — stays pure. The approval key is
    derived from turn + action, so a replay returns the existing task.
    """
    selected_agent = str(state.get("selected_agent", "approval"))
    pending_action = {
        "network": "vpn_api_provision",
        "finance": "finance_submit",
        "approval": "approval_decision",
    }.get(selected_agent, "approval_decision")
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    approval_key = approval_key_for(turn_id, pending_action)
    required_approver_roles = approver_roles_for(selected_agent)

    task = await approval_store.create(
        task_id=approval_id_for(approval_key),
        run_id=turn_id,
        turn_id=turn_id,
        thread_id=turn_id,
        user_id=str(state.get("user_id", "")),
        agent_id=selected_agent,
        intent=state.get("intent"),
        risk_level=state.get("risk_level"),
        rule_id="HIGH-RISK-APPROVAL-REQUIRED",
        approval_key=approval_key,
        required_approver_roles=required_approver_roles,
        resume_payload={
            "pending_action": pending_action,
            "request_id": str(state.get("request_id", "")),
            "turn_id": turn_id,
        },
    )

    emit = _emit(config)
    if emit:
        await emit(
            {
                "type": "tool_result",
                "toolCallId": f"approval_{task.id}",
                "name": "prepare_approval",
                "success": True,
                "message": f"高风险操作已创建审批任务：{task.id}",
            }
        )

    return {
        "approval": {
            "id": task.id,
            "status": "pending",
            "required": True,
            "ruleId": task.rule_id,
            "requiredApproverRoles": task.required_approver_roles,
        },
        "pending_action": pending_action,
        "status": "waiting_approval",
    }


async def execute_approved_action(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    pending_action = str(state.get("pending_action", ""))
    if not pending_action:
        return {}

    selected_agent = str(state.get("selected_agent", "approval"))
    if not authorize_tool(state.get("user_context"), selected_agent, pending_action):
        message = f"RBAC denied write action {pending_action}"
        if emit:
            await emit({"type": "error", "message": message})
        return {
            "status": "failed",
            "guard_error": message,
            "final_answer": message,
        }

    adapter = get_tool_adapter()
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    tool_call_id = f"write_{uuid.uuid4().hex[:12]}"
    approval_id = str((state.get("approval") or {}).get("id") or "") or None
    args = {
        "request_id": state.get("request_id"),
        "user_id": state.get("user_id"),
        "intent": state.get("intent"),
        "risk_level": state.get("risk_level"),
        "latest_user_message": state.get("latest_user_message"),
    }

    if emit:
        await emit(
            {
                "type": "tool_call",
                "toolCallId": tool_call_id,
                "name": pending_action,
                "args": {**args, "effect_key": effect_key_for(turn_id, pending_action)},
            }
        )

    async def invoke(provider_key: str) -> dict[str, Any]:
        call_args = {**args, "idempotency_key": provider_key}
        # Execution rail (before): the model does not get to invent arguments.
        refusal = enforce_tool_rails(pending_action, call_args, {})
        if refusal is not None:
            await record_action(
                "guardrail.tool_input_blocked",
                object_type="tool",
                object_id=pending_action,
                outcome="denied",
                actor_id=str(state.get("user_id") or "") or None,
                detail={"rail": refusal.get("rail_blocked")},
            )
            return refusal

        result = await adapter.execute(pending_action, call_args, state)

        # Execution rail (after): tool output re-enters the model loop.
        refusal = enforce_tool_rails(pending_action, call_args, result)
        if refusal is not None:
            await record_action(
                "guardrail.tool_output_blocked",
                object_type="tool",
                object_id=pending_action,
                outcome="denied",
                actor_id=str(state.get("user_id") or "") or None,
                detail={"rail": refusal.get("rail_blocked")},
            )
            return refusal
        return result

    try:
        result = await effect_ledger.run_once(
            turn_id=turn_id,
            action=pending_action,
            approval_id=approval_id,
            execute=invoke,
        )
    except Exception as exc:
        message = f"写操作执行失败：{exc}"
        if emit:
            await emit({"type": "error", "message": message})
        return {
            "status": "failed",
            "guard_error": message,
            "final_answer": message,
        }

    result = {**result, "toolName": pending_action}

    await record_action(
        "tool.write",
        object_type="tool",
        object_id=pending_action,
        outcome="success" if result.get("success") else "failure",
        actor_id=str(state.get("user_id") or "") or None,
        detail={
            "turn_id": turn_id,
            "approval_id": approval_id,
            "deduplicated": bool(result.get("deduplicated")),
        },
    )

    if emit:
        await emit(
            {
                "type": "tool_result",
                "toolCallId": tool_call_id,
                "name": pending_action,
                "success": result.get("success", False),
                "message": result.get("text", ""),
            }
        )

    return {
        "tool_results": [result],
        "executed_writes": [result],
        "status": "resuming",
    }


async def verify_result(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    if emit:
        await emit(
            {
                "type": "thinking",
                "content": "写操作已完成，正在核对执行结果。",
                "step": True,
            }
        )
    return {}
