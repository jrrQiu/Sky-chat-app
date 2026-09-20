import re
from typing import Any

from langchain_core.runnables import RunnableConfig


def classify_request(text: str) -> dict[str, Any]:
    rules = [
        {
            "pattern": r"(年假|剩余假期|剩余年假|请假.*(查询|余额|还有)|还有.*(天|年假|假期))",
            "intent": "hr.leave.query",
            "agent_id": "hr",
            "action": "query",
            "risk_level": "low",
            "plan": ["retrieve_employee_leave_balance"],
        },
        {
            "pattern": r"(报销.*(状态|进度|查询)|付款状态|发票状态|预算.*(查询|还有|剩余))",
            "intent": "finance.reimbursement.query",
            "agent_id": "finance",
            "action": "query",
            "risk_level": "low",
            "plan": ["query_erp", "check_payment_status"],
        },
        {
            "pattern": r"(vpn|wi-?fi|firewall|防火墙|内网|外网|网络访问).*(状态|查询|进度)",
            "intent": "network.vpn.query",
            "agent_id": "network",
            "action": "query",
            "risk_level": "low",
            "plan": ["query_cmdb", "query_permission"],
        },
        {
            "pattern": r"(approval|审批|approve|reject|通过|拒绝|加签)",
            "intent": "approval.decision.request",
            "agent_id": "approval",
            "action": "approve",
            "risk_level": "high",
            "plan": ["load_rule", "create_approval_task", "wait_for_signal"],
        },
        {
            "pattern": r"(vpn|wi-?fi|firewall|防火墙|内网|外网|网络访问)",
            "intent": "network.vpn.request",
            "agent_id": "network",
            "action": "submit",
            "risk_level": "high",
            "plan": [
                "retrieve_policy",
                "collect_slots",
                "create_approval",
                "execute_network_tool",
            ],
        },
        {
            "pattern": r"(password|密码|解锁|unlock|software|软件|device|设备|install|安装)",
            "intent": "it.service.request",
            "agent_id": "it",
            "action": "submit",
            "risk_level": "medium",
            "plan": ["collect_slots", "check_permission", "execute_it_tool"],
        },
        {
            "pattern": r"(leave|请假|onboarding|入职|certificate|证明|employee|员工|organization|组织)",
            "intent": "hr.service.submit",
            "agent_id": "hr",
            "action": "submit",
            "risk_level": "medium",
            "plan": ["verify_employee", "collect_slots", "execute_hr_tool"],
        },
        {
            "pattern": r"(reimburs|报销|invoice|发票|budget|预算|purchase|采购|payment|付款)",
            "intent": "finance.service.submit",
            "agent_id": "finance",
            "action": "submit",
            "risk_level": "high",
            "plan": [
                "verify_policy",
                "collect_slots",
                "create_approval",
                "execute_finance_tool",
            ],
        },
        {
            "pattern": r"(policy|制度|sop|流程|history|历史|faq|常见问题|怎么|如何|what|how)",
            "intent": "knowledge.retrieve",
            "agent_id": "knowledge",
            "action": "query",
            "risk_level": "low",
            "plan": ["retrieve_policy", "retrieve_history", "answer_with_citations"],
        },
    ]

    for rule in rules:
        if re.search(rule["pattern"], text, re.I):
            return rule

    return {
        "intent": "knowledge.retrieve",
        "agent_id": "knowledge",
        "action": "query",
        "risk_level": "low",
        "plan": ["answer_general_question"],
    }


async def orchestrate(
    state: dict[str, Any],
    config: RunnableConfig | None = None,
):
    result = classify_request(str(state.get("latest_user_message", "")))
    emit = config.get("configurable", {}).get("emit") if config else None

    if emit:
        await emit(
            {
                "type": "thinking",
                "content": (
                    f"Orchestrator 已识别意图：{result['intent']}，"
                    f"正在调度 {result['agent_id']} Agent。"
                ),
                "step": True,
            }
        )

    return {
        "intent": result["intent"],
        "action": result["action"],
        "selected_agent": result["agent_id"],
        "risk_level": result["risk_level"],
        "plan": result["plan"],
    }
