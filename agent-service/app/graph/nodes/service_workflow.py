import logging
import time
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.config import settings
from app.core.audit import record_action
from app.core.effect_ledger import effect_key_for, effect_ledger
from app.core.guardrails import enforce_tool_rails
from app.persistence.approval_store import (
    approval_id_for,
    approval_key_for,
    approval_store,
)
from app.registry.rbac import authorize_agent, authorize_tool
from app.tools.actions import (
    ACTIONS,
    AUTHORITY_SYSTEM,
    action_key,
    describe_outcome,
    evaluate_system_authority,
    refusal_message,
)
from app.tools.actions import resolve as resolve_action

logger = logging.getLogger(__name__)
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
    # The action is derived from the *intent*, not from the agent. An agent serves several
    # intents and only some of them write anything, and the old per-agent guess produced an
    # action name ("approval_decision") for requests that had no external action at all.
    action = resolve_action(state.get("intent"))
    pending_action = action.name if action else ""
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    approval_key = approval_key_for(turn_id, action_key(state.get("intent")))
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


async def _system_authority(
    state: dict[str, Any],
    action: Any,
    approval_id: str | None,
):
    """Read the approval of record and decide whether it authorizes a system execution.

    The store is consulted rather than the graph state on purpose: the state carries the
    decision that was made when the run was interrupted, while the store carries the decision
    that stands now. For an elevation path the difference matters.
    """
    task = None
    if approval_id:
        try:
            task = await approval_store.get(approval_id)
        except Exception as exc:  # a store we cannot read must not become an authorization
            logger.warning("Could not read approval %s: %s", approval_id, exc)
            task = None

    status = getattr(task.status, "value", task.status) if task else None
    return evaluate_system_authority(
        action,
        approval_id=approval_id,
        approval_status=str(status) if status is not None else None,
        decided_by=task.decided_by if task else None,
        approval_action=(task.resume_payload or {}).get("pending_action") if task else None,
        consumed_at=task.consumed_at if task else None,
        created_at=task.created_at if task else None,
        approval_requester=task.user_id if task else None,
        requester=str(state.get("user_id") or "") or None,
        now=time.time(),
        ttl_seconds=settings.system_action_approval_ttl_seconds,
    )


async def execute_approved_action(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    emit = _emit(config)
    pending_action = str(state.get("pending_action", ""))
    turn_id = str(state.get("turn_id") or state.get("request_id", ""))
    selected_agent = str(state.get("selected_agent", "approval"))
    approval_id = str((state.get("approval") or {}).get("id") or "") or None
    requested_action = ACTIONS.get(pending_action)

    if requested_action is None:
        # Nothing external to run. That is a real answer for an approval-decision request,
        # and it is also what an intent with no wired-up action gets -- previously such
        # requests were given the invented action name "approval_decision" and answered with
        # a canned success string.
        message = "审批已通过；该请求没有需要外部系统执行的动作。"
        if emit:
            await emit(
                {
                    "type": "tool_result",
                    "toolCallId": f"write_{uuid.uuid4().hex[:12]}",
                    "name": pending_action or "no-action",
                    "success": True,
                    "message": message,
                }
            )
        return {
            "tool_results": [
                {
                    "toolName": pending_action or "no-action",
                    "success": True,
                    "noActionRequired": True,
                    "text": message,
                }
            ],
            "status": "resuming",
        }

    if requested_action.authority == AUTHORITY_SYSTEM:
        # "Employee requests, administrator approves, the system provisions." The requester is
        # not expected to hold the write permission -- that is the whole point -- so the
        # approval is the control, and it is checked against the store (the authority of record)
        # rather than against the graph state.
        authority = await _system_authority(state, requested_action, approval_id)
        if not authority.allowed:
            message = refusal_message(requested_action, authority.reason or "UNKNOWN")
            await record_action(
                "tool.write",
                object_type="tool",
                object_id=pending_action,
                outcome="denied",
                actor_id=str(state.get("user_id") or "") or None,
                detail={
                    "turn_id": turn_id,
                    "approval_id": approval_id,
                    "reason": authority.reason,
                    "executed_as": "system",
                    "external_executed": False,
                },
            )
            if emit:
                await emit({"type": "error", "message": message})
            return {
                "status": "failed",
                "guard_error": authority.reason,
                "final_answer": message,
            }
        authorizer_id = authority.authorizer_id
    elif not authorize_tool(state.get("user_context"), selected_agent, pending_action):
        # Dual control: the requester's own permission authorizes the action, and the approval
        # only adds a second signature. The refusal is audited because an approved action that
        # silently never ran must still leave a trace.
        message = f"RBAC denied write action {pending_action}"
        await record_action(
            "tool.write",
            object_type="tool",
            object_id=pending_action,
            outcome="denied",
            actor_id=str(state.get("user_id") or "") or None,
            detail={
                "turn_id": turn_id,
                "approval_id": approval_id,
                "reason": "RBAC_DENIED",
                "required_permission": "write action not granted to the requester's roles",
                "external_executed": False,
            },
        )
        if emit:
            await emit({"type": "error", "message": message})
        return {
            "status": "failed",
            "guard_error": message,
            "final_answer": (
                f"审批已通过，但当前账号没有执行「{requested_action.description}」的权限，"
                f"该动作未执行。"
            ),
        }
    else:
        authorizer_id = None

    adapter = get_tool_adapter()
    tool_call_id = f"write_{uuid.uuid4().hex[:12]}"
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

    async def record_change_ticket() -> int | None:
        """Register the approved work in the system we actually own.

        This part is real: it creates a change ticket through the same Java endpoint a
        user-raised ticket goes through, so an approval leaves an auditable trail even when
        the external call cannot be made. It is not a substitute for the external action and
        the outcome text says so.
        """
        if not (requested_action.records_change_ticket and settings.high_risk_records_change_ticket):
            return None
        ticket_args = {
            **args,
            "type": "change",
            "title": f"[变更] {requested_action.description}",
            "description": (
                f"由高风险审批触发。\n审批单：{approval_id or '未记录'}\n"
                f"意图：{state.get('intent')}\n原始请求：{state.get('latest_user_message')}\n"
                f"待执行动作：{pending_action}（目标系统尚未接入）"
            ),
        }
        refusal = enforce_tool_rails("ticket_create", ticket_args, {})
        if refusal is not None:
            return None
        result = await adapter.execute("ticket_create", ticket_args, state)
        if not result.get("success"):
            return None
        data = result.get("data") or {}
        number = data.get("number")
        return int(number) if isinstance(number, (int, float, str)) and str(number).isdigit() else None

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

        # The real, achievable half: register the approved work in our own system.
        ticket_number = await record_change_ticket()

        if not requested_action.external_implemented:
            # The requested action did NOT happen. Saying otherwise was the defect, and the
            # ledger is told so too (`success: False` records a failed effect, which a later
            # run may retry once the target system exists).
            text, error = describe_outcome(requested_action, ticket_number)
            return {
                "success": False,
                "text": text,
                "error": error,
                "changeTicketNumber": ticket_number,
                "externalAction": pending_action,
                "externalExecuted": False,
            }

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
        return {**result, "changeTicketNumber": ticket_number}

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
            "external_executed": bool(result.get("externalExecuted", result.get("success"))),
            "change_ticket_number": result.get("changeTicketNumber"),
            "deduplicated": bool(result.get("deduplicated")),
            "error": result.get("error"),
            # For a system-executed action the requester is only the beneficiary; who authorized
            # it is the fact an investigation needs, so it is recorded next to the effect.
            "executed_as": "system" if requested_action.authority == AUTHORITY_SYSTEM else "requester",
            "authorizer_id": authorizer_id,
            "on_behalf_of": str(state.get("user_id") or "") or None,
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
    if not emit:
        return {}

    writes = state.get("executed_writes") or []
    failed = [w for w in writes if not w.get("success")]
    if failed:
        # The old text claimed the write had completed unconditionally, which read as
        # confirmation of something that had not happened.
        content = (
            "写操作未成功执行："
            + str(failed[0].get("text") or failed[0].get("error") or "未知原因")
        )
    elif writes:
        content = "写操作已执行，正在核对执行结果。"
    else:
        content = "本次没有需要执行的外部动作，正在整理答复。"
    await emit({"type": "thinking", "content": content, "step": True})
    return {}
