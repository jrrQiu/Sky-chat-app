"""Rule-based tool selection: the fallback when the model cannot choose.

This used to be the *only* way tools were chosen, and it decided by regex over the user's
message across a catalog of twenty-odd stubs. Now that the catalog contains five real tools and
the model chooses when it can (function calling), this module has one job: pick a sane default
when no model is available, so the service desk still raises and looks up tickets instead of
doing nothing.

It deliberately cannot select an approval-gated tool. Those are reached through the intent
taxonomy and the approval gate, so a regex can never be the reason a destructive action runs.
"""

from __future__ import annotations

import re

from app.config import settings
from app.tools.catalog import TOOLS, direct_tools

#: What each agent may reach without the model's help. Ticket tools only: they are the only
#: tools that exist.
TOOLS_BY_AGENT: dict[str, list[str]] = {
    "it": ["ticket_create", "ticket_list"],
    "network": ["ticket_create", "ticket_list"],
    "hr": ["ticket_create", "ticket_list"],
    "finance": ["ticket_create", "ticket_list"],
    "knowledge": ["ticket_list"],
    "orchestrator": ["ticket_list"],
    "approval": [],
    "guardian": [],
}

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
    r"坏了|损坏|故障|报障|报修|无法|不能|不可用|打不开|连不上|失败|异常|宕机|离线|"
    r"报错|崩溃|申请|申领|开通|办理|重置|解锁|安装|"
    r"broken|not working|error|fail|request|reset|install",
    re.I,
)


def ticket_tools_for_message(user_message: str, available: list[str]) -> list[str]:
    """Which ticket tools a message implies.

    Kept narrow so a message that is not about tickets never triggers one.
    """
    selected: list[str] = []
    if _TICKET_STATUS_PATTERN.search(user_message) and "ticket_list" in available:
        return ["ticket_list"]
    if _TICKET_NUMBER_PATTERN.search(user_message) and "ticket_list" in available:
        # Look the number up first; a specific ticket is then resolved through the
        # caller-scoped list so an invisible ticket stays invisible.
        return ["ticket_list"]
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
    """The fallback selection. Never returns an approval-gated tool."""
    tools = TOOLS_BY_AGENT.get(agent_id, [])
    if action == "capabilities":
        return []

    selected = ticket_tools_for_message(user_message, tools)
    # A request that looks like a fault gets a ticket even if the agent has no other tool; that
    # is the whole point of a service desk.
    return [name for name in selected if name in TOOLS and name in direct_tools()]
