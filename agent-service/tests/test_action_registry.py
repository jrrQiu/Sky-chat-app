"""What production actually declares, with no test-only actions in the registry.

Kept separate from `test_write_actions.py` on purpose: that file installs synthetic actions to
exercise capabilities no production action uses yet, and a registry whose contents are asserted
while fixtures are adding to it is not asserting anything about production.
"""

from __future__ import annotations

import time

from app.tools import actions as actions_module
from app.tools.actions import (
    ACTIONS,
    AUTHORITY_REQUESTER,
    NOT_IMPLEMENTED,
    action_key,
    action_status,
    describe_outcome,
    evaluate_system_authority,
    resolve,
)


def test_exactly_one_action_is_declared_and_it_is_real():
    assert list(ACTIONS) == ["ticket_merge"]
    action = ACTIONS["ticket_merge"]
    # Implemented against a system that exists, and irreversible -- the source ticket becomes a
    # tombstone -- which is what makes it worth a second signature.
    assert action.external_implemented is True
    assert action.records_change_ticket is False
    # `TicketService.merge` requires a staff caller, so the requester holds the permission and
    # the approval is dual control rather than the system acting for someone who could not.
    assert action.authority == AUTHORITY_REQUESTER
    assert resolve("support.ticket.merge").name == "ticket_merge"


def test_the_fictional_actions_are_gone():
    assert "vpn_api_provision" not in ACTIONS
    assert "finance_submit" not in ACTIONS
    # Their intents resolve to "nothing to execute" instead of to an invented action name.
    assert resolve("network.vpn.request") is None
    assert resolve("finance.service.submit") is None
    assert action_key("network.vpn.request") == "no-action"


def test_readiness_reports_no_system_executed_action():
    status = action_status()

    # `system` execution remains a tested capability, but nothing in production uses it, and
    # saying so is the point: an operator should not have to read the source to find out.
    assert status["systemExecutedActions"] == []
    assert status["externalExecutionAvailable"] is True
    assert [a["name"] for a in status["actions"]] == ["ticket_merge"]


def test_the_write_actions_are_not_reachable_from_the_normal_tool_path():
    """An approved action must not also be selectable as an ordinary tool."""
    from app.tools.selection import TOOLS_BY_AGENT, select_tools_for_request

    for agent, tools in TOOLS_BY_AGENT.items():
        assert "ticket_merge" not in tools, agent

    for message in ("合并工单", "帮我把 #12 合并到 #14", "我重复提交了两张工单", "帮我合并"):
        for agent in ("it", "network", "hr", "finance", "orchestrator", "knowledge", "approval"):
            assert "ticket_merge" not in select_tools_for_request(agent, message), (agent, message)


def test_a_dual_control_action_needs_no_approval_to_authorize_it():
    decision = evaluate_system_authority(
        ACTIONS["ticket_merge"],
        approval_id=None,
        approval_status=None,
        decided_by=None,
        approval_action=None,
        consumed_at=None,
        created_at=None,
        approval_requester=None,
        requester="user-1",
        now=time.time(),
        ttl_seconds=3600,
    )
    assert decision.allowed is True


def test_outcome_text_separates_recorded_from_executed():
    text, error = describe_outcome(ACTIONS["ticket_merge"], ticket_number=None)
    assert error is None
    assert "已完成" in text
    assert NOT_IMPLEMENTED  # the constant the not-integrated path uses
