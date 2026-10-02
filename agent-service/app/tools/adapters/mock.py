import re
import time
from typing import Any

from app.config import settings
from app.knowledge.base import format_knowledge_context, search_knowledge
from app.persistence.approval_store import (
    approval_id_for,
    approval_key_for,
    approval_store,
)


#: The *stub* tool set per agent. Ticket tools are deliberately NOT listed here:
#: anything in this map is selected unconditionally for that agent, and a weather
#: question must not raise a ticket. Ticket tools are added only by
#: :func:`ticket_tools_for_message`, and their authorization comes from
#: ``agents.yaml`` (which agent may use them) plus ``TOOL_PERMISSIONS`` (which user
#: may).
TOOLS_BY_AGENT: dict[str, list[str]] = {
    "orchestrator": ["agent_registry", "policy_api"],
    "it": ["idp", "mdm", "software_center"],
    "network": ["cmdb", "permission_system", "vpn_api"],
    "hr": ["hris", "organization_service", "form_service"],
    "finance": ["erp", "ocr", "budget_service"],
    "knowledge": ["pgvector", "elasticsearch", "reranker"],
    "approval": ["rule_engine", "temporal", "notification_system"],
    "guardian": ["rbac", "dlp", "audit_service"],
}

#: Tools implemented for real (they call the Java service). Everything else in
#: ``TOOLS_BY_AGENT`` is still a stub, and results are marked ``simulated``.
TICKET_TOOLS: tuple[str, ...] = ("ticket_create", "ticket_get", "ticket_list", "ticket_comment")

#: The user asking about their own tickets rather than describing a new problem.
_TICKET_STATUS_PATTERN = re.compile(
    r"我的工单|我提的工单|工单(?:状态|进度|列表|情况)|处理到哪|什么进度|进展如何|"
    r"my tickets?|ticket status",
    re.I,
)
#: A ticket number the user wants looked up ("#12", "工单 12").
_TICKET_NUMBER_PATTERN = re.compile(r"#\s*(\d+)|工单\s*(?:号|编号)?\s*(\d+)", re.I)
#: Something to raise a ticket about.
_SERVICE_ISSUE_PATTERN = re.compile(
    r"坏了|损坏|故障|报障|报修|无法|不能|不可用|打不开|连不上|连不上|失败|异常|宕机|离线|"
    r"报错|崩溃|申请|申领|开通|办理|重置|解锁|安装|"
    r"broken|not working|error|fail|request|reset|install",
    re.I,
)


def ticket_tools_for_message(user_message: str, available: list[str]) -> list[str]:
    """Which ticket tools a message implies.

    Rule-based on purpose: this service has no model function-calling, so the choice
    is made here and reported as ``tool_routing: rule-based`` in ``/ready``. Kept
    narrow so a message that is not about tickets never triggers one.
    """
    selected: list[str] = []
    if _TICKET_STATUS_PATTERN.search(user_message) and "ticket_list" in available:
        selected.append("ticket_list")
        return selected
    if _TICKET_NUMBER_PATTERN.search(user_message) and "ticket_list" in available:
        # Look the number up first; ticket_get lives behind that lookup so an
        # invisible ticket stays invisible.
        selected.append("ticket_list")
        return selected
    if (
        getattr(settings, "ticket_auto_create_from_chat", True)
        and _SERVICE_ISSUE_PATTERN.search(user_message)
        and "ticket_create" in available
    ):
        selected.append("ticket_create")
    return selected


def select_tools_for_request(
    agent_id: str,
    user_message: str,
    action: str = "query",
) -> list[str]:
    tools = TOOLS_BY_AGENT.get(agent_id, [])
    ticket_tools = (
        ticket_tools_for_message(user_message, _declared_tools(agent_id))
        if action != "capabilities"
        else []
    )

    if agent_id == "it":
        selected: list[str] = []
        if re.search(r"password|密码|unlock|解锁|账号", user_message, re.I):
            selected.append("idp")
        if re.search(r"software|软件|install|安装", user_message, re.I):
            selected.append("software_center")
        if re.search(r"device|设备|fault|故障", user_message, re.I):
            selected.append("mdm")
        selected = selected or tools
        return [*selected, *ticket_tools]

    if agent_id == "hr":
        base = ["hris"] if action == "query" else ["hris", "organization_service", "form_service"]
        return [*base, *ticket_tools]

    if agent_id == "network":
        base = ["cmdb", "permission_system"] if action == "query" else list(tools)
        return [*base, *ticket_tools]

    if agent_id == "finance":
        selected = ["erp"]
        if re.search(r"invoice|发票|ocr|票据", user_message, re.I):
            selected.append("ocr")
        if re.search(r"budget|预算|采购|purchase", user_message, re.I):
            selected.append("budget_service")
        return [*selected, *ticket_tools]

    if agent_id == "knowledge":
        return ["pgvector", "reranker", *ticket_tools]

    return [*tools, *ticket_tools]


def _declared_tools(agent_id: str) -> list[str]:
    """The tools the registry allows this agent to use.

    Read from ``agents.yaml`` rather than from the stub map above, because that is
    what ``can_agent_use_tool`` will enforce at execution time.
    """
    from app.registry.registry import get_agent

    return list(get_agent(agent_id).get("tools", []))


async def execute_mock_tool(
    tool_name: str,
    args: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    user_message = str(args.get("latest_user_message", ""))
    action = str(state.get("action", "query"))
    user_context = state.get("user_context")

    async def approval_result(
        rule_id: str,
        agent_id: str,
        pending_action: str,
    ) -> dict[str, Any]:
        turn_id = str(state.get("turn_id") or state.get("request_id", ""))
        key = approval_key_for(turn_id, pending_action)
        task = await approval_store.create(
            task_id=approval_id_for(key),
            run_id=str(state.get("request_id", "")),
            turn_id=turn_id,
            thread_id=turn_id,
            user_id=str(state.get("user_id", "")),
            agent_id=agent_id,
            intent=state.get("intent"),
            risk_level=state.get("risk_level"),
            rule_id=rule_id,
            approval_key=key,
            resume_payload={
                "pending_action": pending_action,
                "target_system": "finance-erp",
                "request_id": str(state.get("request_id", "")),
                "turn_id": turn_id,
            },
        )
        return {
            "success": True,
            "text": f"审批单已创建：{task.id}",
            "approval": {
                "id": task.id,
                "status": "pending",
                "required": True,
                "ruleId": rule_id,
                "resumeAction": pending_action,
            },
        }

    if tool_name in {"vpn_api", "temporal"}:
        if tool_name == "vpn_api":
            return await approval_result(
                "NETWORK-VPN-REQUIRE-DUAL-APPROVAL",
                "network",
                "vpn_api_provision",
            )
        return await approval_result(
            "APPROVAL-TEMPORAL-TASK",
            "approval",
            "approval_decision",
        )

    handlers = {
        "agent_registry": lambda: {
            "success": True,
            "text": f"已选择 {state.get('selected_agent', 'knowledge')} Agent。",
        },
        "policy_api": lambda: {
            "success": True,
            "text": format_knowledge_context(
                user_message,
                user_context=user_context,
            ),
            "citations": [
                doc.id
                for doc in search_knowledge(
                    user_message,
                    user_context=user_context,
                )
            ],
        },
        "idp": lambda: {
            "success": True,
            "text": "已生成密码重置链接并校验账号状态。",
            "data": {"ticket_id": "IT-IDP-10086"},
        },
        "mdm": lambda: {
            "success": True,
            "text": "已读取设备状态：MacBook 在线，系统版本合规。",
            "data": {"device_id": "macbook-001", "status": "online"},
        },
        "software_center": lambda: {
            "success": True,
            "text": "软件安装任务已创建，等待设备同步。",
            "data": {"task_id": "SW-2001"},
        },
        "cmdb": lambda: {
            "success": True,
            "text": "已补齐设备、网络和所属组织信息。",
            "data": {"device_id": "macbook-001", "network_zone": "office"},
        },
        "permission_system": lambda: {
            "success": True,
            "text": "当前用户具备访问申请条件，仍需审批确认。",
            "data": {"allowed": True},
        },
        "vpn_api_provision": lambda: {
            "success": True,
            "text": "VPN 审批已通过，账号和配置已开通。",
            "data": {"provisioned": True, "vpn_account": "vpn-user-001"},
        },
        "hris": lambda: {
            "success": True,
            "text": (
                "已查询员工剩余年假：5 天。"
                if action == "query"
                else "已读取员工档案，身份校验通过。"
            ),
            "data": (
                {"employee_id": "EMP-001", "remaining_annual_leave": 5}
                if action == "query"
                else {"employee_id": "EMP-001"}
            ),
        },
        "organization_service": lambda: {
            "success": True,
            "text": "已查询所属组织、主管和 HRBP。",
            "data": {"department": "finance", "manager": "MGR-001"},
        },
        "form_service": lambda: {
            "success": True,
            "text": "人事表单已生成并提交。",
            "data": {"form_id": "HRF-001"},
        },
        "erp": lambda: {
            "success": True,
            "text": (
                "当前没有待付款记录，最近一笔报销状态为已提交。"
                if action == "query"
                else "已创建报销单并进入审批队列。"
            ),
            "data": (
                {"payment_status": "submitted"}
                if action == "query"
                else {"payment_status": "pending_approval"}
            ),
        },
        "ocr": lambda: {
            "success": True,
            "text": "发票识别完成，金额已提取。",
            "data": {"invoice_amount": "1280.00"},
        },
        "budget_service": lambda: {
            "success": True,
            "text": "已检查部门预算，当前预算充足。",
            "data": {"remaining_budget": "85000"},
        },
        "pgvector": lambda: {
            "success": True,
            "text": format_knowledge_context(
                user_message,
                user_context=user_context,
            ),
            "citations": [
                doc.id
                for doc in search_knowledge(
                    user_message,
                    user_context=user_context,
                )
            ],
        },
        "elasticsearch": lambda: {
            "success": True,
            "text": format_knowledge_context(
                user_message,
                user_context=user_context,
            ),
            "citations": [
                doc.id
                for doc in search_knowledge(
                    user_message,
                    user_context=user_context,
                )
            ],
        },
        "reranker": lambda: {
            "success": True,
            "text": "已按相关性重排知识片段。",
            "citations": [
                doc.id
                for doc in search_knowledge(
                    user_message,
                    user_context=user_context,
                    limit=2,
                )
            ],
        },
        "rule_engine": lambda: {
            "success": True,
            "text": "已命中审批规则。",
            "data": {
                "requires_approval": state.get("risk_level") == "high"
                or str(state.get("intent", "")).startswith("approval")
            },
        },
        "finance_submit": lambda: {
            "success": True,
            "text": "财务审批已通过，报销单已提交并进入付款流程。",
            "data": {"payment_status": "submitted", "erp_task_id": "ERP-2001"},
        },
        "approval_decision": lambda: {
            "success": True,
            "text": "审批流程已完成，通知和审计记录已写入。",
            "data": {"completed": True},
        },
        "notification_system": lambda: {
            "success": True,
            "text": "已通知审批人。",
            "data": {"notified": True},
        },
        "rbac": lambda: {
            "success": True,
            "text": "当前用户权限检查通过。",
            "data": {"allowed": True},
        },
        "dlp": lambda: {
            "success": True,
            "text": "未发现敏感数据泄露。",
            "data": {"blocked": False},
        },
        "audit_service": lambda: {
            "success": True,
            "text": "审计日志已写入。",
            "data": {"audit_id": f"AUD-{int(time.time() * 1000)}"},
        },
    }

    handler = handlers.get(tool_name)
    if handler:
        return handler()

    return {"success": False, "text": f"未知工具：{tool_name}"}
